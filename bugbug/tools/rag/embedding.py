from transformers import AutoTokenizer, AutoModel
from sentence_transformers import SentenceTransformer
import torch

import bugbug.tools.rag.pick_call as my_pick

def encoder_starencode():
    MODEL_NAME = "bigcode/starencoder"

    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModel.from_pretrained(MODEL_NAME)
    
    MASK_TOKEN = "<mask>"
    SEPARATOR_TOKEN = "<sep>"
    PAD_TOKEN = "<pad>"
    CLS_TOKEN = "<cls>"
    tokenizer.add_special_tokens({"pad_token": PAD_TOKEN})
    tokenizer.add_special_tokens({"sep_token": SEPARATOR_TOKEN})
    tokenizer.add_special_tokens({"cls_token": CLS_TOKEN})
    tokenizer.add_special_tokens({"mask_token": MASK_TOKEN})
    
    def get_emb(input):
        input = torch.tensor(tokenizer([input])['input_ids'])
        output = model(input[:,:1024])
        
        return output['pooler_output'].tolist()[0]
    
    def get_emb_saver(input, filename=None):
        if filename is None:
            return get_emb(input)
            
        return my_pick.run_and_pickle(get_emb, {'input':input}, filename, do_print=False)
    return get_emb_saver
    
def encoder_sentence_trans():
    MODEL='Frazic/udever-bloom-3b-sentence'

    encoder = SentenceTransformer(MODEL)
    
    def get_emb(input):
        return encoder.encode(input).tolist()
    
    def get_emb_saver(input, filename=None):
        if filename is None:
            return get_emb(input)
        
        return my_pick.run_and_pickle(get_emb, {'input':input}, filename, do_print=False)
    return get_emb_saver