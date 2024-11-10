from __future__ import annotations

import asyncio
from typing import Any
import json

import websockets
from websockets.client import WebSocketClientProtocol
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from .message_handlers import AgentReceiver
import logging
logger = logging.getLogger('neosphere').getChild(__name__)
logger.setLevel(logging.DEBUG)


class AsyncWebSocketApp(object):
    url: str
    reciever: AgentReceiver
    ws: WebSocketClientProtocol

    _is_connected: bool
    _listening: bool
    _authorize_called: bool
    _retry_count: int
    _err_state: bool
    MAX_RETRIES = 10
    GAP_BETWEEN_RETRIES_SEC = 2

    def __init__(self, url: str, recvr: AgentReceiver) -> None:
        self.url = url
        self.reciever = recvr
        self._authorize_called = False
        self._listening = False
        self._is_connected = False
        self._retry_count = 0
        self._err_state = False
        self.recv_active = []
        self.send_active = []

    def _clean_state(self):
        self._is_connected = False
        self._listening = False
        self._err_state = False

    async def connect(self):
        self._clean_state()
        self._err_state = False
        await self.reciever.before_connect()
        try:
            self.ws = await websockets.connect(self.url)
        except ConnectionRefusedError as e:
            logger.error(f'Connection refused: {e}')
            return
        except asyncio.TimeoutError as e:
            logger.error(f'Timeout when connecting: {e}')
            return
        await self.reciever.set_websocket(self.ws)
        await self.reciever.on_connect()
        self._is_connected = True
    
    async def authorize(self):
        if not self._is_connected:
            logger.error("Cannot authorize as not connected")
            return
        await self.reciever.on_authorize()
        self._authorize_called = True

    async def disconnect(self):
        self._is_connected = False
        await self.reciever.before_disconnect()
        await self.ws.close()
        await self.reciever.on_disconnect()

    async def send(self, message: str) -> Any:
        await self.ws.send(message)

    async def ws_recv_message(self) -> str | None:
        try:
            return await asyncio.wait_for(self.ws.recv(), 1)

        except asyncio.TimeoutError:
            return None

    async def ws_recv_loop(self):
        while self._is_connected:
            try:
                if not self._listening:
                    self._listening = True 
                message = await self.ws.recv()
            except ConnectionClosedError as e:
                logger.error(f'Recieve loop closed with error: {e.reason}, code: {e.code}')
                self._err_state = True
                await self.reciever.client_handler.send(None)
                break
            except ConnectionClosedOK as e:
                logger.info(f'Recieve loop closed without error: {e}')
                # this will prevent retrying to open the connection.
                self._retry_count = self.MAX_RETRIES + 1
                self._is_connected = False
                await self.reciever.client_handler.send(None)
                break
            if message is None:
                continue

            # call on_message async function as a task and keep moving
            # await self.reciever.on_message(message)
            task = asyncio.create_task(self.reciever.on_message(message))
            self.recv_active.append(task)
        logger.info(f"Recieve loop ending. Will wait for all incoming messages ({len(self.recv_active)}) to finish.")
        if len(self.recv_active) > 0:
            await asyncio.gather(*self.recv_active)
        logger.info(f"Recieve loop ended.")

    async def ws_send_loop(self):
        while self._is_connected:
            if not self._listening:
                # sleep till listening is enabled
                await asyncio.sleep(0.2)
                continue
            if self._err_state:
                logger.debug("Send loop closed as error state is set")
                break
            if self.reciever.recieved_pull_the_plug:
                logger.warning("Send loop closed as we recieved the 'Pull the Plug' signal")
                break
            # then listen to the message queue
            message = await self.reciever.client_handler.get()
            if message is None:
                continue
            # logger.debug(f"Sending message: {message['cmd']}")
            logger.debug(f"Sending message: {message}")
            task = asyncio.create_task(self.ws.send(json.dumps(message)))
            self.send_active.append(task)
        logger.info(f"Send loop ending. Will wait for all outgoing messages ({len(self.send_active)}) to finish.")
        if len(self.send_active) > 0:
            await asyncio.gather(*self.send_active)
        logger.info(f"Send loop ended.")

    async def run(self):
        while self._retry_count <= self.MAX_RETRIES:
            await self.connect()
            await self.authorize()
            await asyncio.gather(self.ws_recv_loop(), self.ws_send_loop())
            # wait for the above coroutines to finish - indicates end
            logger.info("Receiver and Sender loops ended.")
            if self._retry_count >= self.MAX_RETRIES:
                logger.info("Max retries reached. Exiting.")
                break
            else:
                logger.info(f"Retrying ({self._retry_count}/{self.MAX_RETRIES}) connection in {self.GAP_BETWEEN_RETRIES_SEC} seconds.")
                await asyncio.sleep(self.GAP_BETWEEN_RETRIES_SEC)
            self._retry_count = self._retry_count + 1

    def asyncio_run(self):
        try:
            asyncio.run(self.run())

        except KeyboardInterrupt:
            logger.info('Correct exit')
