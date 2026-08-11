from langgraph.graph import StateGraph, START, END
from langgraph.types import Command
from typing import Literal
from langchain.chat_models import init_chat_model

from email_assistant.prompts import agent_system_prompt, default_background, default_response_preferences, default_cal_preferences, AGENT_TOOLS_PROMPT, triage_system_prompt, default_triage_instructions, triage_user_prompt
from email_assistant.schemas import State, StateInput, RouterSchema
from email_assistant.tools.email_tools import write_email, schedule_meeting, check_calendar_availability, Done
from email_assistant.utils import parse_email, tools_by_name

from dotenv import load_dotenv
load_dotenv(".env")

# Initializa LLM for agent
tools = [write_email, schedule_meeting, check_calendar_availability, Done]
tools_map = tools_by_name(tools)
llm = init_chat_model("deepseek-v4-flash", temperature=0.0,extra_body={"thinking": {"type": "disabled"}})
llm_with_tools = llm.bind_tools(tools, tool_choice="any")

# Nodes
def llm_call(state:State):
    """Let LLM decide whether to call a tool or finish the workflow"""
    return {
        "messages": [
            llm_with_tools.invoke(
                [
                    {"role": "system", "content": agent_system_prompt.format(
                        tools_prompt=AGENT_TOOLS_PROMPT,
                        background=default_background,
                        response_preferences=default_response_preferences, 
                        cal_preferences=default_cal_preferences)
                    }           
                ]
                + state["messages"]
            )
        ]
    }

def tool_handler(state:State):
    """Perform the tool call based on the LLM's decision"""
    result = []
    for tool_call in state["messages"][-1].tool_calls:
        tool_name = tool_call["name"]
        tool = tools_map[tool_name]
        observation = tool.invoke(tool_call["args"])
        result.append({"role": "tool", "content" : observation, "tool_call_id": tool_call["id"]})
    return {"messages": result}


# Conditional edge
def should_continue(state:State)->Literal["tool_handler","__end__"]:
    """Route to Tool handler if tool call, End if tool call done"""
    message = state['messages']
    last_message = message[-1]
    if last_message.tool_calls:
        tool_call = last_message.tool_calls[0]
        if tool_call["name"] == "Done":
            return END
        else:
            return "tool_handler"

# Workflow
agent_builder = StateGraph(State)

#add nodes
agent_builder.add_node("llm_call", llm_call)
agent_builder.add_node("tool_handler", tool_handler)


#add edges
agent_builder.add_edge(START, "llm_call")
agent_builder.add_conditional_edges(
    "llm_call",
    should_continue,
    {
        "tool_handler": "tool_handler",
        END: END,
    },
)
agent_builder.add_edge("tool_handler", "llm_call")

agent = agent_builder.compile()

# Initialize the LLM for use with router
llm = init_chat_model("deepseek-v4-flash", temperature=0.0,extra_body={"thinking": {"type": "disabled"}})
llm_router = llm.with_structured_output(RouterSchema) 

# Triage router
def triage_router(state: State) -> Command[Literal["response_agent", "__end__"]]:
    """Analyze email content to decide if we should respond, notify, or ignore."""
    author, to, subject, email_thread = parse_email(state["email_input"])
    system_prompt = triage_system_prompt.format(
        background=default_background,
        triage_instructions=default_triage_instructions
    )
    user_prompt = triage_user_prompt.format(
        author=author, to=to, subject=subject, email_thread=email_thread
    )

    print("收到的 state：", state)
    print("收到的 email_input：", state["email_input"])


    # Run the router LLM
    result = llm_router.invoke(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    )
    # Decision
    classification = result.classification

    if classification == "respond":
        print("📧 Classification: RESPOND - This email requires a response")
        goto = "response_agent"
        # Add the email to the messages
        update = {
            "classification_decision": result.classification,
            "messages": [{"role": "user",
                            "content": f"Respond to the email: {state['email_input']}"
                        }],
        }
    elif result.classification == "ignore":
        print("🚫 Classification: IGNORE - This email can be safely ignored")
        update =  {
            "classification_decision": result.classification,
        }
        goto = END
    elif result.classification == "notify":
        # If real life, this would do something else
        print("🔔 Classification: NOTIFY - This email contains important information")
        update = {
            "classification_decision": result.classification,
        }
        goto = END
    else:
        raise ValueError(f"Invalid classification: {result.classification}")
    return Command(goto=goto, update=update)


# Add a router
overall_workflow = (
    StateGraph(State, input_schema=StateInput)
    .add_node(triage_router)
    .add_node("response_agent", agent)
    .add_edge(START, "triage_router")
)
email_assistant = overall_workflow.compile()