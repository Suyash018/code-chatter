from src.shared.llms.models import get_openai_mini_model
from langchain_core.runnables import RunnablePassthrough
from langchain_core.prompts import ChatPromptTemplate
from src.shared.logging import setup_logging

import pydantic

logger = setup_logging("response_formatter", level="INFO")


class ResponseFormatterResult(pydantic.BaseModel):
    response: str = pydantic.Field(description="The response to show the user.")
    suggestive_pills: list[str] = pydantic.Field(
        description="Questions that the user can ask or the answer to your question that you ask the user. Less than 3 pills and each pill should be less than 5 words."
    )


class ResponseFormatter:
    def __init__(self):
        logger.info("Initializing ResponseFormatter with mini model")
        self.system_prompt = """
        You are a formatter that formats the response into json format.

        Format the response into json format.
        {{
            response: The response to show the user.
            suggestive_pills : Questions that the user can ask or the answer to your question that you ask the user.
        }}

        -----------------------------------------------------
        Response to format will be provided by the user.
        """
        self.prompt_template = ChatPromptTemplate([
            ("system", self.system_prompt),
            ("user", "{response}")
        ])

        self.model = get_openai_mini_model()
        logger.info("Using response formatter model: %s", self.model.model_name)
        self.model_with_structure = self.model.with_structured_output(ResponseFormatterResult)
        self.chain = (
            {"response": RunnablePassthrough()}
            | self.prompt_template
            | self.model_with_structure
        )

    async def format_response(self, response: str) -> dict:
        """Response formatter node."""
        result = await self.chain.ainvoke({"response": response})
        return result.model_dump()
