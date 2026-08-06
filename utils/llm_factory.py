import os
import random
import logging
from dotenv import load_dotenv

load_dotenv(override=True)
logger = logging.getLogger(__name__)

try:
    from langchain_openai import AzureChatOpenAI
except ImportError:
    AzureChatOpenAI = None

try:
    from langchain_groq import ChatGroq
except ImportError:
    ChatGroq = None

from config.settings import settings

_key_index = 0


def get_llm(temperature=0.0, max_tokens=None, agent_name=None, **kwargs):
    """
    Factory function returning AzureChatOpenAI (gpt41mini) as primary LLM provider,
    with fallback to round-robin Groq (llama-3.1-8b-instant).
    """
    global _key_index
    load_dotenv(override=True)

    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT") or getattr(settings, "AZURE_OPENAI_ENDPOINT", "")
    azure_key = os.getenv("AZURE_OPENAI_API_KEY") or getattr(settings, "AZURE_OPENAI_API_KEY", "")
    azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME") or getattr(settings, "AZURE_OPENAI_DEPLOYMENT_NAME", "gpt41mini")
    azure_version = os.getenv("AZURE_OPENAI_API_VERSION") or getattr(settings, "AZURE_OPENAI_API_VERSION", "2024-10-21")

    # Primary: AzureChatOpenAI
    if AzureChatOpenAI and azure_key and azure_endpoint:
        try:
            return AzureChatOpenAI(
                azure_endpoint=azure_endpoint,
                api_key=azure_key,
                azure_deployment=azure_deployment,
                api_version=azure_version,
                temperature=temperature,
                max_tokens=max_tokens or 700,
                max_retries=3,
                **kwargs
            )
        except Exception as e:
            logger.warning(f"AzureChatOpenAI initialization failed: {e}. Falling back to Groq.")

    # Fallback: Round-robin Groq
    groq_keys = os.getenv("GROQ_API_KEYS") or getattr(settings, "GROQ_API_KEYS", "")
    if ChatGroq and groq_keys:
        keys = [k.strip() for k in groq_keys.split(",") if k.strip()]
        if keys:
            api_key = keys[_key_index % len(keys)]
            _key_index += 1
            return ChatGroq(
                model="llama-3.1-8b-instant",
                api_key=api_key,
                temperature=temperature,
                max_tokens=max_tokens or 700,
                max_retries=3,
                **kwargs
            )

    raise RuntimeError("No valid LLM Provider (Azure OpenAI or Groq) available!")
