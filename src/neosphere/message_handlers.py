from __future__ import annotations

import asyncio
import time
from typing import Any, List
import uuid

from websockets.client import WebSocketClientProtocol

import logging

from .contacts import Contacts
from .media_handler import MediaHandler
logger = logging.getLogger('neosphere').getChild(__name__)
import json
import traceback

class AgentHandler(asyncio.Queue):
    """
    Handler handles intents that are usually outgoing requests. 
    It then adds the message to the instance's queue if it's a 
    query that needs to be tracked for response.
    """
    def __init__(self, query_index, name, *args, **kwargs) -> None:
        # if media_handler in kwargs then read it into a variable and unset it
        if 'media_handler' in kwargs:
            self.media_handler = kwargs.pop('media_handler')
        else:
            self.media_handler = None
        super().__init__(*args, **kwargs)
        self.query_index = query_index
        self.backoff_signal_template = {'cmd': 'err', 'text': 'w8'}
        self.hold_signal_template = {'cmd': 'err', 'text': 'hold'}
        self.name = name

    async def send(self, item: Any) -> None:
        await self.put(item)

    async def recv(self) -> Any:
        return await self.get()
    
    def get_query_index(self):
        return self.query_index

    def register_media_handler(self, media_handler: MediaHandler):
        self.media_handler = media_handler
    
    def register_contacts_handler(self, contacts: Contacts):
        self.contacts = contacts
    
    async def get_media(self, media_id)->str:
        if self.media_handler:
            return await self.media_handler.get_media(media_id)
        else:
            logger.error(f"No media handler registered, to get {media_id}.")
            return None
    
    async def get_medias(self, *media_ids):
        media_list = []
        if len(media_ids) == 1 and isinstance(media_ids[0], list):
            # Called as foo(a, b) where b is a list [b1, b2, b3]
            media_id_list = media_ids[0]
        else:
            # Called as foo(a, b1, b2, b3)
            media_id_list = media_ids
        if self.media_handler:
            for media_id in media_id_list:
                media_list.append(await self.media_handler.get_media(media_id))
        else:
            logger.error(f"No media handler registered, to get {media_id}.")
        return media_list
    
    async def create_forward_copy(self, forward_to_id, *media_ids):
        new_list = []
        if len(media_ids) == 1 and isinstance(media_ids[0], list):
            # Called as foo(a, b) where b is a list [b1, b2, b3]
            media_id_list = media_ids[0]
        else:
            # Called as foo(a, b1, b2, b3)
            media_id_list = media_ids
        if self.media_handler:
            for media_id in media_id_list:
                new_id = await self.media_handler.create_forward_copy_id(media_id, forward_to_id)
                new_list.append(new_id)
        else:
            logger.error(f"No media handler registered, to forward {media_id} to {forward_to_id}.")
        return new_list
    
    async def save_media(self, parent_id, media_file)->str:
        if self.media_handler:
            return await self.media_handler.save_media(parent_id, media_file)
        else:
            logger.error(f"No media handler registered, to save media.")
            return None

    def save_medias(self, parent_id, media_files):
        """
        Save multiple media files at once.

        Parameters:
            parent_id (str): The ID of the parent media.
            media_files (list): A list of file-like objects containing media data.

        Returns:
            list: A list of media IDs for the saved media.
        """
        media_ids = []
        for media_file in media_files:
            media_id = self.save_media(parent_id, media_file)
            media_ids.append(media_id)
        return media_ids

    async def send_token_request(self, connection_code, agent_share_id, client_name):
        auth_data: dict = {
            'cmd': 'aiagent',
            'code': connection_code, # You get this from the app from your AI Agent's profile
            'id': agent_share_id, # The agent's ID, displayed on Agent profile as niopub.com/x/john.doe
            'client_id': client_name
        }
        await self.send(auth_data)
    
    async def send_token_to_reconnect(self, token, agent_share_id):
        token_conn = {
            'cmd': 'aiagent',
            'token': token,
            'id': agent_share_id,
        }
        await self.send(token_conn)

    async def respond_to_group_message(self, group_id, response_data, media_ids: List[str]=[], choices: List[str]=[]):
        group_message = {
            'cmd': 'group-response',
            'group_id': group_id,
            'text': response_data
        }
        if media_ids:
            group_message['data_ids'] = media_ids
        if choices:
            group_message['choices'] = choices
        #put response in send queue
        await self.send(group_message)
    
    async def query_agent(self, agent_id, query, media_ids: List[str]=[]):
        # generate a uuid without dashes
        query_id = agent_id + str(uuid.uuid4())[8].replace('-', '')
        query_created = {
            'cmd': 'query',
            'to_id': agent_id,
            'query_id': query_id,
            'text': query
        }
        if media_ids:
            query_created['data_ids'] = media_ids
        # overwrites prev sent record if a query ID is re-used
        self._record_in_query_tracker(query_id, query_created)
        #put query in send queue
        await self.send(query_created)
        return query_id
    
    def _record_in_query_tracker(self, query_id, query: dict):
        self.query_index[query_id] = {
            'sent_on': int(time.time()),
            # 'text': query
        }
        return query_id
    
    async def send_backoff_signal(self, to_id):
        """
        If the network sees multiple backoff signals from you to the same agent, 
        we will eventually put the agent on a 30s hold from sending you messages.

        Useful when an agent is sending too many queries and you want to slow them down.
        """
        err_signal = self.backoff_signal_template.copy()
        err_signal['to_id'] = to_id
        await self.send(err_signal)
    
    async def put_a_30s_hold(self, group_id):
        """
        Putting a 30s hold on a group will prevent any user in the group from 
        sending queries to the agent for 30s.

        Useful when an agent is getting too many queries from a group and you want to slow them down.
        """
        hold_signal = self.hold_signal_template.copy()
        hold_signal['group_id'] = group_id
        await self.send(hold_signal)

    async def _record_query_response_recvd(self, query_id, response: Message):
        """
        This is an internal helper function to record the response received for a query in the query_index.

        This method is intended to be used by the connection handler to record the response received for a query
        and should not be called directly by the client (our AI agent).
        """
        # check if query_id exists in query_index
        if query_id in self.query_index:
            logger.warning(f"Got response for query ID {query_id} (from agent {response.from_id}).")
            # add response to query_index
            self.query_index[query_id]['response_rcv'] = response
        else:
            # create new record
            logger.warning(f"Got response for query ID {query_id} (from agent {response.from_id}). But query is missing from query_index. Dropping the response and sending a lost query signal.")
            await self.send_backoff_signal(response.from_id)
            return
    
    async def wait_for_query_response(self, query_id, timeout=10, check_interval=0.5) -> Message:
        """
        Given a query_id, this function will wait for the response to the query.

        The wait logic is simple, it checks if the query_id is in the query_index and if it has a response_rcv key.
        The response_rcv key is added to the query_index when a response is received for the query_id on the 
        connection. If the response is not received within the timeout, the function will return None.
        """
        start_time = int(time.time())
        while True:
            logger.info(f"Checking for query response for query ID: {query_id}...")
            if query_id in self.query_index:
                if 'response_rcv' in self.query_index[query_id]:
                    resp = self.query_index[query_id]['response_rcv']
                    del self.query_index[query_id]
                    return resp
            else:
                # query_id is not in query_index, nothing to wait for
                logger.error(f"Query ID {query_id} not found, nothing to wait for.")
                return None
            if int(time.time()) - start_time > timeout:
                logger.warning(f"Timeout while waiting for query response for query ID: {query_id}")
                return None
            await asyncio.sleep(check_interval)
    
    async def respond_to_agent_query(self, agent_id, query_id, response_data, media_ids: List[str]=[]):
        """
        Send a response to a query from another agent.

        Requires the query_id you received in the original query to be sent back with this response.
        """
        query_created = {
            'cmd': 'ans',
            'to_id': agent_id,
            'query_id': query_id,
            'text': response_data
        }
        if media_ids:
            query_created['data_ids'] = media_ids
        #put query in send queue
        await self.send(query_created)

class Message:
    def __init__(self, **kwargs) -> None:
        # check if token in kwargs
        self.token = kwargs.get('token', None)
        self.text: str = kwargs.get('text', None)
        self.data_ids = kwargs.get('data_ids', []) 
        self.from_id = kwargs.get('from_id', None)
        self.group_id = kwargs.get('group_id', None)
        self.query_id = kwargs.get('query_id', None)
        self.is_resp = kwargs.get('is_resp', False)
        self.is_err = kwargs.get('is_err', False)
        
    def to_dict(self):
        if self.token:
            return {
                'token': self.token
            }
        return {
            'text': self.text,
            'data_ids': self.data_ids,
            'from_id': self.from_id,
            'group_id': self.group_id,
            'query_id': self.query_id,
            'is_resp': self.is_resp,
            'is_err': self.is_err,
        }

    def to_json(self):
        return json.dumps(self.to_dict())
    
    def __str__(self):
        return self.to_dict()
    
    def _compare_text(self, text):
        if self.text and self.text == text:
            return True
        return False
    
    def is_pull_the_plug(self):
        return self._compare_text('close') and self.from_id == 'sys' and self.group_id == 'sys'
    
    def is_from_owner(self):
        return self.group_id == 'owner'

    @staticmethod
    def from_json(json_str):
        message_dict = json.loads(json_str)
        return Message.from_dict(message_dict)
    
    @staticmethod
    def from_dict(message_dict):
        return Message(**message_dict)

class AgentReceiver:
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
        self.group_message_receiver = group_message_receiver
        self.query_receiver = query_receiver
        self.reconnection_token = None
        self.context_to_forward = context
        # query tracker is any simple key value structure the client will use to keep a track of queries sent and their responses received.
        # this allows the client to provide a blocking wait for a query response.
        self.query_tracker = {}
        self.client_handler = AgentHandler(query_index=self.query_tracker, name=agent_share_id)
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
                    await self.group_message_receiver(msg, self.client_handler, **self.context_to_forward)
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
                    await self.query_receiver(msg, self.client_handler)
        except Exception as e:
            logger.error(f"Error while processing message: {e}")
            traceback.print_exc()
            return