import asyncio
import logging
import json
from typing import Any, Optional

from websockets import connect as ws_connect
from websockets.client import WebSocketClientProtocol
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from neosphere.client_api import Message, NeosphereClient
from neosphere.contacts import Contacts
from neosphere.media_handler import MediaHandler
logger = logging.getLogger('neosphere').getChild(__name__)
import traceback

class NeosphereAgent:
    """
    A client agent for the Neosphere network that manages WebSocket connections,
    message handling, and callback triggering for both human and AI messages.
    
    This class abstracts the logic required to:
    - Connect and authorize with the Neosphere server.
    - Process incoming messages and dispatch them via provided callbacks.
    - Handle reconnections and error states gracefully.
    
    Parameters:
        agent_share_id: Unique identifier for the agent.
        connection_code: Authentication code for establishing connection.
        client_name: The display name for the client.
        group_message_receiver: Callback to process group messages from human users.
        query_receiver: Callback to process queries from other AI agents.
        contacts: Initial contacts to use for message handling.
        **context: Additional context passed to callbacks.
    """
    app: Any
    ws: WebSocketClientProtocol

    def __init__(self, 
                 agent_share_id, 
                 connection_code, 
                 client_name, 
                 group_message_receiver, 
                 query_receiver,
                 contacts=[],
                 **context) -> None:
        self.agent_share_id = agent_share_id
        self.connection_code = connection_code
        self.client_name = client_name
        self.human_group_msg_callback = group_message_receiver
        self.ai_query_msg_callback = query_receiver
        self.reconnection_token = None
        self.context_to_forward = context
        # query tracker is any simple key value structure the client will use to keep a track of queries sent and their responses received.
        # this allows the client to provide a blocking wait for a query response.
        self.query_tracker = {}
        self.client_handler = NeosphereClient(query_index=self.query_tracker, name=agent_share_id)
        self.recieved_pull_the_plug = False
        self.initial_contacts = contacts


    async def set_websocket(self, ws: WebSocketClientProtocol):
        self.ws = ws

    async def before_connect(self):
        pass

    async def attempt_reconnect(self):
        pass

    async def on_connect(self):
        pass
    
    async def on_authorize(self):
        if not self.reconnection_token:
            logger.info("Authorizing agent connection with connection code")
            await self.client_handler.send_token_request(self.connection_code, self.agent_share_id, self.client_name)
        else:
            logger.info("Restarting agent connection with connection token")
            await self.client_handler.send_token_to_reconnect(self.reconnection_token, self.agent_share_id)
    
    async def before_disconnect(self):
        logger.info('before_disconnect')

    async def on_disconnect(self):
        logger.info('on_disconnect')
    
    async def on_message(self, message: str):
        msg = Message.from_json(message)
        logger.debug(f"Received processed message: {msg.to_dict()}")
        try:
            if msg.is_pull_the_plug():
                logger.info("Received pull the plug signal. Closing connection.")
                self.recieved_pull_the_plug = True
                await self.client_handler.send(None)
            if msg.token:
                self.reconnection_token = msg.token
                self.client_handler.register_media_handler(MediaHandler(self.reconnection_token, "/tmp/neosphere_media"))
                self.client_handler.register_contacts_handler(Contacts(self.reconnection_token, self.initial_contacts))
                # initialize contacts
                # Contacts().initial_public_contacts(public_contacts)
                logger.info(f"Received a connection token. Registered media handler and fetched ({self.client_handler.contacts.get_contact_count()}) contacts.")
            if msg.is_err:
                logger.error(f"Received error message.")
            if msg.group_id:
                if not msg.is_err:
                    logger.info('Message is group message')
                    await self.human_group_msg_callback(msg, self.client_handler, **self.context_to_forward)
                else:
                    logger.error(f"(Group={msg.group_id}) Error from: {msg.from_id}. Error: {msg.text}")
            elif msg.query_id:
                if msg.is_resp and not msg.is_err:
                    logger.info('Message is a query resp')
                    await self.client_handler._record_query_response_recvd(msg.query_id, msg, **self.context_to_forward)
                elif msg.is_err:
                    logger.error(f"(Agent) Error from: {msg.get('from_id')}. Error: {msg.get('text')}")
                else:
                    logger.info('Message is a query from another agent')
                    await self.ai_query_msg_callback(msg, self.client_handler)
        except Exception as e:
            logger.error(f"Error while processing message: {e}")
            traceback.print_exc()
            return


class NeosphereAgentTaskRunner(object):
    """
    Manages the lifecycle of a NeosphereAgent's WebSocket connection.
    
    This class handles:
    - Establishing and cleaning up Neosphere connections.
    - Authorizing the agent after connecting.
    - Running separate asynchronous loops for sending and receiving messages.
    - Retrying connection attempts up to a defined maximum.
    
    Parameters:
        agent: An instance of NeosphereAgent to run.
        url: Optional URL for the Neosphere server (defaults to "wss://n10s.net/stream/ai").
    """
    
    url: str
    agent: NeosphereAgent
    ws: WebSocketClientProtocol

    _is_connected: bool
    _listening: bool
    _authorize_called: bool
    _retry_count: int
    _err_state: bool
    MAX_RETRIES = 10
    GAP_BETWEEN_RETRIES_SEC = 2

    def __init__(self, agent: NeosphereAgent, url: str = None) -> None:
        self.server_url = url if url else "wss://n10s.net/"
        self.url = self.server_url + "stream/ai"
        self.agent = agent
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
        await self.agent.before_connect()
        try:
            self.ws = await ws_connect(self.url)
        except ConnectionRefusedError as e:
            logger.error(f'Connection refused: {e}')
            return
        except asyncio.TimeoutError as e:
            logger.error(f'Timeout when connecting: {e}')
            return
        await self.agent.set_websocket(self.ws)
        await self.agent.on_connect()
        self._is_connected = True
    
    async def authorize(self):
        if not self._is_connected:
            logger.error("Cannot authorize as not connected")
            return
        await self.agent.on_authorize()
        self._authorize_called = True

    async def disconnect(self):
        self._is_connected = False
        await self.agent.before_disconnect()
        await self.ws.close()
        await self.agent.on_disconnect()

    async def send(self, message: str) -> Any:
        await self.ws.send(message)

    async def ws_recv_message(self) -> Optional[str]:
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
                # sending None triggers an iteration in the send loop
                await self.agent.client_handler.send(None)
                break
            except ConnectionClosedOK as e:
                logger.info(f'Recieve loop closed without error: {e}')
                # this will prevent retrying to open the connection.
                self._retry_count = self.MAX_RETRIES + 1
                self._is_connected = False
                # sending None triggers an iteration in the send loop
                await self.agent.client_handler.send(None)
                break
            if message is None:
                continue

            # call on_message async function as a task and keep moving
            # await self.reciever.on_message(message)
            task = asyncio.create_task(self.agent.on_message(message))
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
            if self.agent.recieved_pull_the_plug:
                logger.warning("Send loop closed as we recieved the 'Pull the Plug' signal")
                break
            # then listen to the message queue
            message = await self.agent.client_handler.get()
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
