import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

load_dotenv()



# ─── LLM Model Factories ─────────────────────────────────


def get_openai_model(model_name: str | None = None) -> ChatOpenAI:
    return ChatOpenAI(
        model=model_name or os.getenv("DEFAULT_MODEL", "gpt-5.2-2025-12-11"),
        api_key=os.getenv("OPENAI_API_KEY"),
    )


def get_openai_mini_model(model_name: str | None = None) -> ChatOpenAI:
    return ChatOpenAI(
        model=model_name or os.getenv("DEFAULT_MINI_MODEL", "gpt-5-mini-2025-08-07"),
        api_key=os.getenv("OPENAI_API_KEY"),
    )


def get_openai_embeddings(model_name: str | None = None) -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model=model_name or os.getenv("DEFAULT_EMBEDDING_MODEL", "text-embedding-3-large"),
        api_key=os.getenv("OPENAI_API_KEY"),
    )

def get_enrichment_model(model_name: str | None = None) -> ChatOpenAI:
    return ChatOpenAI(
        model=model_name or os.getenv("ENRICHMENT_MODEL", "gpt-5-mini-2025-08-07"),
        api_key=os.getenv("OPENAI_API_KEY"),
    )