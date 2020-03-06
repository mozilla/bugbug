import sys
# to load bugbug_http.utils, when running from project root
sys.path.append(".")

from bugbug_http.utils import IdleTTLCache
from datetime import timedelta
import time
import os

def integration_test_cache_thread():
  cache = IdleTTLCache(timedelta(seconds = 5))
  cache['test_key'] = 'payload'
  cache.start_ttl_thread()
  time.sleep(2)
  assert('test_key' in cache)
  time.sleep(5)
  assert('test_key' not in cache)
  print("test succeded")

if __name__ == '__main__':
  integration_test_cache_thread()
  os._exit(0)

