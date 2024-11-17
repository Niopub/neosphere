import logging
import requests
import time
logger = logging.getLogger('neosphere').getChild(__name__)

class SingletonMeta(type):
    """A metaclass for creating singleton classes."""
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            instance = super().__call__(*args, **kwargs)
            cls._instances[cls] = instance
        return cls._instances[cls]

class ContactsFetcher(metaclass=SingletonMeta):

    def __init__(self, agent_names):
        if not hasattr(self, '_initialized'):
            self._initialized = True
            self.agent_names = set(agent_names)
            self.public_cache = {}
            self.private_cache = {}
            self._fetch_all_agents()

    def _fetch_all_agents(self):
        """Fetch information for all agents and cache it."""
        self._fetch_and_cache_agents(list(self.agent_names))

    def _fetch_and_cache_agents(self, agent_names, private=False):
        """Fetch information for a list of agents from the API and cache it."""
        payload = {"names": agent_names}
        if private:
            payload["private"] = True
        response = requests.post('https://api.example.com/contact/public', json=payload)
        if response.status_code == 200:
            data = response.json()
            for agent in data:
                agent_info = {
                    'name': agent['name'],
                    'description': agent['description'],
                    'owned': False,
                    'ts': time.time()
                }
                if 'input' in agent:
                    agent_info['input'] = agent['input']
                if 'owned' in agent:
                    agent_info['owned'] = True
                if agent.get('public', False):
                    self.public_cache[agent['name']] = agent_info
                else:
                    self.private_cache[agent['name']] = agent_info
        else:
            logger.error(f"Failed to fetch contacts with HTTP code: {response.status_code}")
            return

    def add_agent(self, agent_name):
        """Add an agent name to the list and fetch its data."""
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

    def get_tool_schema(self, agent_names=None, backend=None, include_public=True, include_private=True, include_owned=False):
        """Generate tool schemas for agents, returning public, private, or both schemas as a list."""
        if not include_public and not include_private:
            raise ValueError("At least one of 'include_public' or 'include_private' must be True.")

        current_time = time.time()
        agents_to_fetch = [agent_names] if agent_names else set(self.public_cache.keys()).union(self.private_cache.keys())

        for agent in agents_to_fetch:
            # 3 cases for cache misses
            if agent in self.public_cache and current_time - self.public_cache[agent].get('timestamp', 0) > 300:
                self._fetch_and_cache_agents([agent])
            elif agent in self.private_cache and current_time - self.private_cache[agent].get('timestamp', 0) > 300:
                self._fetch_and_cache_agents([agent])
            else:
                self._fetch_and_cache_agents([agent])

        schemas = []
        for agent in agents_to_fetch:
            if agent in self.public_cache:
                agent_info = self.public_cache[agent]
                if include_owned and agent_info['owned']:
                    schemas.append(self._generate_schema(agent_info, backend))
                elif include_public:
                    schemas.append(self._generate_schema(agent_info, backend))
            if agent in self.private_cache:
                agent_info = self.private_cache[agent]
                if include_owned and agent_info['owned']:
                    schemas.append(self._generate_schema(agent_info, backend))
                elif include_private:
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
                "name": agent_data["name"],
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
                    "name": agent_data["name"],
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

# Example Usage
if __name__ == "__main__":
    agents = ["WeatherBot", "StockAnalyzer"]
    fetcher = ContactsFetcher(agents)
    
    # Add a new agent and fetch data
    fetcher.add_agent("NewsBot")
    
    # Get tool schema for a specific agent
    tool_schema = fetcher.get_tool_schema("WeatherBot", backend="anthropic")
    print(tool_schema)
    
    # Get all tool schemas (public and private)
    all_schemas = fetcher.get_tool_schema()
    print(all_schemas)
    
    # Get only public tool schemas
    public_schemas = fetcher.get_tool_schema(include_public=True, include_private=False)
    print(public_schemas)
    
    # Get only private tool schemas
    private_schemas = fetcher.get_tool_schema(include_public=False, include_private=True)
    print(private_schemas)
    
    # Remove an agent
    fetcher.remove_agent("StockAnalyzer")
