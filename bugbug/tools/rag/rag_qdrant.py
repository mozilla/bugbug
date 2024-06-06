from qdrant_client import models, QdrantClient
import bugbug.tools.rag.embedding as embedding
import bugbug.tools.rag.pick_call as my_pick
import bugbug.tools.rag.filter_data as filter_data
import json
import numpy as np 

import numpy as np 

COLLECTION = 'ubi_revcom'
FOLDER_SAVE = None #folder where to save the embedding. Does not save if is None.

review_rag_encoder = {
    "starencoder": embedding.encoder_starencode,
    "SentenceTransformer": embedding.encoder_sentence_trans,
}

class RAGObject(): 
    def __init__(self, data_file, fun_embedding, num_ex):
        self.data = load_data(data_file)
        
        # COLUMNS NEEDED IN DATASET TO RUN:
        assert np.all([e in self.data.columns for e in ['body', 'diff', 'info_text', 'info_dir']])
        # body: body of the diff (lines) with no information on line position and filenames
        # diff: diff formated for prompt with all information
        # info_text: only text of the comments 
        # info_dir: comments in the json format for prompt

        self.data = filter_data.filter_data(self.data, 'info_text')
        self.data = [{str(what):str(self.data.iloc[i][what]) for what in self.data.columns} for i in range(len(self.data))]
        
        self.get_hits = rag_approach(self.data, 'body', review_rag_encoder[fun_embedding])
        
        self.num_ex = num_ex 
        
    def get_examples(self, review):
        examples = {}
        for e in review:
            hits = self.get_hits(review[e], self.num_ex+2)
            for hit in hits:
                if hit.payload['filediff_id'] not in examples:
                    examples[hit.payload['filediff_id']] = {'payload':hit.payload, 'score':0}
                examples[hit.payload['filediff_id']]['score'] += hit.score
        l = [(examples[e]['score'], examples[e]['payload']) for e in examples]
        l.sort()
        
        return l[-self.num_ex:]  
          
def rag_approach(documents, target_X, fun_embedding=embedding.encoder_starencode):
    encoder = fun_embedding()
    client = QdrantClient(":memory:")
    
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config=models.VectorParams(
            size = len(encoder('test')),
            distance=models.Distance.COSINE,
        ),
    )
    
    l_points = [
        models.PointStruct(
            id=idx, vector=encoder(doc[target_X],
                                f"{FOLDER_SAVE}{fun_embedding.__name__}_{doc['filediff_id']}.p" if FOLDER_SAVE is not None else None),
            payload=doc
        )
        for idx, doc in enumerate(documents)
    ]
    
    client.upload_points(
        collection_name=COLLECTION,
        points=l_points,
    )

    def get_hits(x, num=3):
        hits = client.search(
            collection_name=COLLECTION,
            query_vector=encoder(x),
            limit=num,
        )
        return hits
        
    return get_hits

# Need to have a data loader for mozilla too
def load_data(data_file):
    return my_pick.pickle_load(data_file)
