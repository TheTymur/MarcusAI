import inspect
from typing import Callable, List

def generate_openai_tool_schema(func: Callable) -> dict:
    """
    Reads a Python function and automatically generates the OpenAI-compatible 
    JSON schema required for tool calling.
    """

    name = func.__name__
    description = inspect.getdoc(func) or f"Executes the {name} function."
    

    sig = inspect.signature(func)
    parameters = {
        "type": "object",
        "properties": {},
        "required": []
    }
    

    for param_name, param in sig.parameters.items():
        if param_name == 'self':
            continue # Skip 'self' for class methods
            
        param_type = "string"
        if param.annotation == int:
            param_type = "integer"
        elif param.annotation == bool:
            param_type = "boolean"
            
        parameters["properties"][param_name] = {
            "type": param_type,
            "description": f"The {param_name} parameter."
        }
        
        # If the parameter doesn't have a default value, it's required
        if param.default == inspect.Parameter.empty:
            parameters["required"].append(param_name)
            
    # 4. Return the strict format 
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters
        }
    }

def get_tools_and_map(functions: List[Callable]):
    """
    Takes a list of Python functions and returns two things:
    1. The list of JSON schemas to send to the LLM.
    2. A dictionary mapping the function name to the actual Python function so we can run it later.
    """
    tools = []
    function_map = {}
    for func in functions:
        tools.append(generate_openai_tool_schema(func))
        function_map[func.__name__] = func
    return tools, function_map