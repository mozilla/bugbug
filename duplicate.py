#Using latent semantic indexing and similarity matrix 

import logging
logging.basicConfig(format='%(asctime)s : %(levelname)s : %(message)s', level=logging.INFO)

import nltk
nltk.download('stopwords')
from nltk.corpus import stopwords
from nltk.stem.porter import PorterStemmer
from gensim.corpora import Dictionary
from gensim import models
from gensim import similarities

from bugbug import bugzilla

corpus = []

query = int(input('Enter bug id : '))

for bug in bugzilla.get_bugs():
    corpus.append([bug['id'],bug['summary']])

#cleaning the text    
ps = PorterStemmer()
texts = [[ps.stem(word) for word in summary.lower().split() if word not in set(stopwords.words('english'))] for bug_id,summary in corpus]

#Assigning unique integer ids to all words
dictionary = Dictionary(texts)

#conversion to bow
corpus_final = [dictionary.doc2bow(text) for text in texts]

tfidf = models.TfidfModel(corpus_final)  #initializing the tfidf transformation model
corpus_tfidf = tfidf[corpus_final]   #applying the model on same corpus,resultant corpus is of same dimension

#transform tfidf corpus to latent 300-D space via LATENT SEMANTIC INDEXING
lsi = models.LsiModel(corpus_tfidf, id2word=dictionary, num_topics=300)
corpus_lsi = lsi[corpus_tfidf]

#indexing the corpus
index = similarities.Similarity(output_prefix = 'simdata.shdat',corpus = corpus_lsi,num_features = 400)

#query
for bug_id,summary in corpus:
    if bug_id == query:
        query = [ps.stem(word) for word in summary.lower().split() if word not in set(stopwords.words('english'))] 
        break
vec_bow = dictionary.doc2bow(query)
vec_lsi = lsi[vec_bow]

# perform a similarity query against the corpus
sims = index[vec_lsi]
sims = sorted(enumerate(sims), key=lambda item: -item[1])

#bug_id of the 10 most similar summaries
for i,j in enumerate(sims):
    print('bug_id of {}th most similar bug: {}'.format(i+1,corpus[j[0]][0]))
    if i == 9:
        break
