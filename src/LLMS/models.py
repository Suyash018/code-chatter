import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

load_dotenv()



# ─── LLM Model Factories ─────────────────────────────────


def get_openai_model() -> ChatOpenAI:
    return ChatOpenAI(
        model="gpt-5.2-2025-12-11",
        api_key=os.getenv("OPENAI_API_KEY"),
    )


def get_openai_embeddings() -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model="text-embedding-3-large",
        api_key=os.getenv("OPENAI_API_KEY"),
    )
