import logging
import requests
import time

from neosphere.media_handler import get_headers
logger = logging.getLogger('neosphere').getChild(__name__)

class SingletonMeta(type):
    """A metaclass for creating singleton classes."""
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            instance = super().__call__(*args, **kwargs)
            cls._instances[cls] = instance
        return cls._instances[cls]

class Contacts(metaclass=SingletonMeta):
    """
    This class converts Niopub agents information into tool schemas for use with function
    calling in LLMs. 
    
    Currently this class supports translation of Niopub agent descriptions to tool schemas for both
    Claude and ChatGPT. The tool schema is a JSON object that describes how your LLM
    can use other agent to solve a specific task and is derived from the Description
    and Input fields of the agent on Niopub app. These fields can be modified on the app
    by the user who owns the agent.

    This allows our local agents to integrate with other online Niopub agents using 
    an LLM's tool calling (aka function calling) capabilities.
    """
    agent_names = set()
    def __init__(self, reconn_token, agent_names):
        if not hasattr(self, '_initialized'):
            self._initialized = True
            self.public_cache = {}
            self.private_cache = {}
            self.token = reconn_token
            self.initial_public_contacts(agent_names)

    def initial_public_contacts(self, agent_names):
        self.agent_names = set(agent_names)
        self._fetch_all_agents()

    def _fetch_all_agents(self):
        """Fetch information for all agents and cache it."""
        logger.debug("Fetching all agent contact info")
        self._fetch_and_cache_agents(list(self.agent_names), include_owned=True)

    def _fetch_and_cache_agents(self, requested_agent_names, include_owned=False):
        """Fetch information for a list of agents from the API and cache it."""
        payload = {"share_ids": requested_agent_names}
        if include_owned:
            payload["owned"] = True
        logger.debug(f"Fetching agent contact info for (if online and accepting queries): {requested_agent_names}")
        response = requests.post('https://n10s.net/post/agent/contacts', json=payload, headers=get_headers(self.token))
        # log response
        if response.status_code == 200:
            data = response.json()
            for agent in data:
                agent_info = {
                    'share_id': agent['share_id'],
                    'description': agent['description'],
                    'owned': False,
                    'ts': time.time()
                }
                if 'input_desc' in agent:
                    agent_info['input_desc'] = agent['input_desc']
                if 'owned' in agent:
                    agent_info['owned'] = True
                if agent.get('open_to_public', False):
                    self.public_cache[agent['share_id']] = agent_info
                else:
                    self.private_cache[agent['share_id']] = agent_info
        else:
            logger.error(f"Failed to fetch contacts with HTTP code: {response.status_code}")
            return
    
    def get_contact_count(self):
        return len(self.public_cache)+len(self.private_cache)

    def add_agent(self, agent_name):
        """Add an agent name to the list and fetch its data."""
        logger.debug(f"Adding agent {agent_name}")
        if agent_name not in self.agent_names:
            self.agent_names.add(agent_name)
            self._fetch_and_cache_agents([agent_name])

    def remove_agent(self, agent_name):
        """Remove an agent name from the list and cache."""
        if agent_name in self.agent_names:
            self.agent_names.remove(agent_name)
            if agent_name in self.public_cache:
                del self.public_cache[agent_name]
            if agent_name in self.private_cache:
                del self.private_cache[agent_name]

    def get_tool_schema(self, agent_names=None, backend=None, only_public_agents=True, only_private_agents=True, self_owned_agents=False):
        """
        Generate tool schemas for agents. These agents can be public, private, or the agents you own. 
        The schema is returned as a list.

        Params:
            agent_names: List of public agent names to fetch. Can be None if we want to fetch only owned or private agents.
            backend: The backend to generate the schema for. Currently supports 'anthropic' and 'openai'.
            only_public_agents: Only add public agents in the resultant schema.
            only_private_agents: Only add private agents in the resultant schema.
            self_owned_agents: Include all agents that you own in the schema.
        
        Returns:
            List of tool schemas for the agents.
        """
        if not only_public_agents and not only_private_agents and not self_owned_agents:
            raise ValueError("At least one of 'only_public_agents' or 'only_private_agents' or 'self_owned_agents' must be True.")
        if backend not in ["anthropic", "openai"]:
            raise ValueError("Unsupported backend specified. Use 'anthropic' or 'openai'.")

        current_time = time.time()
        agents_needed = [agent_names] if agent_names else set(self.public_cache.keys()).union(self.private_cache.keys())
        agents_to_fetch = []
        fetch_owned = self_owned_agents

        # first check local cache
        for agent in agents_needed:
            # 3 cases for cache misses that we need to fetch
            if agent in self.public_cache and current_time - self.public_cache[agent].get('ts', 0) > 300:
                agents_to_fetch.append(agent)
            elif agent in self.private_cache and current_time - self.private_cache[agent].get('ts', 0) > 300:
                fetch_owned = True
            else:
                # should fetch if not in any cache
                agents_to_fetch.append(agent)
        logger.debug(f"Fetching agents for tools schema: {agents_to_fetch}, include_owned: {fetch_owned}")
        self._fetch_and_cache_agents(agents_to_fetch, include_owned=fetch_owned)

        schemas = []
        for agent in agents_needed:
            if agent in self.public_cache:
                agent_info = self.public_cache[agent]
                if self_owned_agents and agent_info['owned']:
                    schemas.append(self._generate_schema(agent_info, backend))
                elif only_public_agents:
                    schemas.append(self._generate_schema(agent_info, backend))
            if agent in self.private_cache:
                agent_info = self.private_cache[agent]
                if self_owned_agents and agent_info['owned']:
                    schemas.append(self._generate_schema(agent_info, backend))
                elif only_private_agents:
                    schemas.append(self._generate_schema(agent_info, backend))
        return schemas

    def _generate_schema(self, agent_data, backend):
        """Helper method to generate a tool schema from agent data, following the schema pattern for both Claude and ChatGPT."""
        input_description = agent_data.get('input', {
            "type": "string",
            "description": f"A natural language query requesting the bot to perform its function: {agent_data['description']}"
        })

        if backend == "anthropic":
            schema = {
                "name": agent_data["share_id"],
                "description": agent_data["description"],
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "input": input_description
                    },
                    "required": ["input"]
                }
            }
        elif backend == "openai":
            schema = {
                "type": "function",
                "function": {
                    "name": agent_data["share_id"],
                    "description": agent_data["description"],
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "input": input_description
                        },
                        "required": ["input"],
                        "additionalProperties": False
                    }
                }
            }
        else:
            raise ValueError("Unsupported backend specified. Use 'anthropic' or 'openai'.")
        
        return schema