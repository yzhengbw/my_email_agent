from langgraph.graph import MessagesState
from typing_extensions import TypedDict, Literal
from pydantic import BaseModel, Field

class State(MessagesState):
    email_input:dict
    classification_decision:Literal["ignore", "respond", "notify"]
    
class StateInput(TypedDict):
    # This is the input to the state
    email_input: dict

class RouterSchema(BaseModel):
    """Analyze the unread email and route it according to its content."""

    reasoning: str = Field(
        description="Step-by-step reasoning behind the classification."
    )
    classification: Literal["ignore", "respond", "notify"] = Field(
        description="The classification of an email: 'ignore' for irrelevant emails, "
        "'notify' for important information that doesn't need a response, "
        "'respond' for emails that need a reply",
    )