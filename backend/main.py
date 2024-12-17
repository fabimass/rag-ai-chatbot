from http.client import HTTPException
import uuid
from fastapi import FastAPI
from config import rag_config, sql_config
from modules.models import QuestionModel, AnswerModel, FeedbackModel, State
from modules.agent_rag import AgentRag
from modules.agent_sql import AgentSql
from modules.supervisor import Supervisor
from modules.utils import get_table_client
from azure.data.tables import TableEntity
from langchain_core.runnables import RunnableLambda
from langgraph.graph import StateGraph


# Agents instantiation
agent_rag = AgentRag(rag_config)
agent_sql = AgentSql(sql_config)
agents = ["agent_rag", "agent_sql"]

# Supervisor instantiation
supervisor = Supervisor(agents)

# Graph instantiation
builder = StateGraph(State)
builder.add_node("supervisor_node", supervisor.pick_next_agent)
builder.add_node("summarizer_node", supervisor.summarize)
builder.add_node("rag_node", agent_rag.generate_answer)
builder.add_node("sql_node", agent_sql.generate_answer)
builder.add_conditional_edges(
    "supervisor_node",
    RunnableLambda(lambda inputs: inputs["next"]),  
    {"agent_rag": "rag_node", "agent_sql": "sql_node", "FINISH": "summarizer_node"}
)
builder.add_edge("rag_node", "supervisor_node")
builder.add_edge("sql_node", "supervisor_node")
builder.set_entry_point("supervisor_node")
graph = builder.compile()

# Tables instantiation
feedback_table = get_table_client("Feedback")
history_table = get_table_client("ChatHistory") 

# Entry point to use FastAPI
app = FastAPI()


# This endpoint returns the user prompt, for testing purposes
@app.get("/api/ping")
def ping():
    return "pong"


# This endpoint receives a prompt and generates a response
@app.post("/api/ask")
def generate_answer(body: QuestionModel):
    session_id = body.session_id
    prompt = body.question

    # Retrieve conversation history or start a new one
    session_history = get_chat_history(session_id)

    try:
        result = graph.invoke({ "question": prompt })
        #add_to_chat_history(AnswerModel(**{"question": prompt, "answer": answer, "session_id": session_id}))
        return {"question": prompt, "answer": result["answer"], "agents": {key: value for key, value in result.items() if key.startswith("agent_")}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {e}")


# This endpoint receives feedback from the user
@app.post("/api/feedback")
async def store_feedback(body: FeedbackModel):
    entity = TableEntity()
    entity["PartitionKey"] = "likes" if body.like else "hates"
    entity["RowKey"] = str(uuid.uuid4())
    entity["Question"] = body.question
    entity["Answer"] = body.answer
    entity["SessionId"] = body.session_id

    # Insert the entity into the Azure Table
    try:
        feedback_table.create_entity(entity=entity)
        return {"message": "Feedback stored successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {e}")


# This endpoint returns the number of likes and hates
@app.get("/api/feedback")
async def get_feedback_count():
    try:
        # Query for "likes" entries
        likes_count = len(list(feedback_table.query_entities(query_filter="PartitionKey eq 'likes'")))
        
        # Query for "hates" entries
        hates_count = len(list(feedback_table.query_entities(query_filter="PartitionKey eq 'hates'")))

        return {"likes": likes_count, "hates": hates_count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {e}")


# This endpoint returns the chat history for a given session id
@app.get("/api/history/{session_id}")
def get_chat_history(session_id):
    entities = history_table.query_entities(query_filter=f"PartitionKey eq '{session_id}'")
    
    # Sort the entities by timestamp
    sorted_entities = sorted(
            (dict(entity, Timestamp=entity.metadata["timestamp"]) for entity in entities),
            key=lambda x: x["Timestamp"]
        )
    
    # Return the latest 2 question-answer pairs
    filtered_entities = sorted_entities[-4:]

    processed_entities = [
        {**{k: v for k, v in d.items() if k != "Timestamp" and k != "RowKey" and k != "PartitionKey"}}
        for d in filtered_entities
    ]

    return processed_entities


# This endpoint adds a new chat to the chat history for a given session id
@app.post("/api/history")
def add_to_chat_history(body: AnswerModel):
    try:
        # Insert the entity for the user question
        user_entity = TableEntity()
        user_entity["PartitionKey"] = body.session_id
        user_entity["RowKey"] = str(uuid.uuid4())
        user_entity["role"] = "user"
        user_entity["content"] = body.question
        history_table.create_entity(entity=user_entity)

        # Insert the entity for the bot answer
        bot_entity = TableEntity()
        bot_entity["PartitionKey"] = body.session_id
        bot_entity["RowKey"] = str(uuid.uuid4())
        bot_entity["role"] = "bot"
        bot_entity["content"] = body.answer
        history_table.create_entity(entity=bot_entity)
        
        return {"message": "Chat history updated successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {e}") 


# This endpoint deletes the chat history for a given session id
@app.delete("/api/history/{session_id}")
def delete_chat_history(session_id):
    entities = history_table.query_entities(f"PartitionKey eq '{session_id}'")
    count = 0
    for entity in entities:
        history_table.delete_entity(
            partition_key=entity["PartitionKey"],
            row_key=entity["RowKey"]
        )
        count += 1
    return {"message": f"Deleted {count} records successfully."}