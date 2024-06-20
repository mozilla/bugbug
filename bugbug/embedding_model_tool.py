from abc import ABC, abstractmethod

from langchain_openai import OpenAIEmbeddings
from openai import AzureOpenAI
from bugbug.utils import get_secret    

class EmbeddingModelTool(ABC): 
    name:str 
    size:int
    
    @abstractmethod
    def embed_query(self, input):
        ...

class OpenAIEmbeddingModelTool(EmbeddingModelTool):
    def __init__(self):
        self.encoder = OpenAIEmbeddings(model="text-embedding-3-large")
        
        self.name = 'openai_3-large'
        self.size = 3072
        
    def embed_query(self, input):
        return self.encoder.embed_query(input)


class AzureOpenAIEmbeddingModelTool(EmbeddingModelTool): 
    def __init__(self):
        self.client = AzureOpenAI(
            azure_endpoint=get_secret("OPENAI_API_ENDPOINT"),
            api_key=get_secret("OPENAI_API_KEY"),
            api_version=get_secret("OPENAI_API_VERSION")
        )
        self.azure_deployment = get_secret("OPENAI_API_DEPLOY_EMB")
        self.name = 'openai_emb'
        self.size = 3072
        
    def embed_query(self, input):
        r =  self.client.embeddings.create(
            input = input,
            model=self.azure_deployment 
        )
        
        return r.data[0].embedding
        
        
embedding_class = {
    "openai": OpenAIEmbeddingModelTool,
    "azureopenai": AzureOpenAIEmbeddingModelTool,
}
