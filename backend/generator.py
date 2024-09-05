from langchain_openai import AzureChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough

class Generator():

    def __init__(self, retriever):
        # Instantiate a pre-trained Large Language Model from Azure OpenAI
        self.llm = AzureChatOpenAI(
            deployment_name="gpt-4o",
            api_version="2023-06-01-preview"
        )

        # The system prompt guides the model on how to respond
        self.system_prompt = (
            "You are an assistant for question-answering tasks."
            "Use the following pieces of retrieved context to answer the question." 
            "If you don't know the answer, say that you don't know." 
            "Use three sentences maximum and keep the answer concise."
            "\n\n"
            "{context}"
        )

        # The prompt puts together the system prompt with the user question
        self.prompt = ChatPromptTemplate.from_messages(
            [
                ("system", self.system_prompt),
                ("human", "{question}"),
            ]
        )

        # The parser just plucks the string content out of the LLM's output message
        self.parser = StrOutputParser()

        # The chain orchestrates the whole flow
        self.rag_chain = (
            {"context": retriever.invoke, "question": RunnablePassthrough()}
            | self.prompt
            | self.llm
            | self.parser
        )


    def invoke(self, user_question):
        # Run the chain and returns the answer generated by the LLM
        answer = self.rag_chain.invoke({"input": user_question})
        return answer