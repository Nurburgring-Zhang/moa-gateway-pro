"""Verify 5 new modules import OK."""
import sys
sys.path.insert(0, '.')
from moa_gateway.capability import rag_search, plan_act, channels, reference_router, checkpoint
print('all 5 modules import OK')
for m in [rag_search, plan_act, channels, reference_router, checkpoint]:
    pub = [x for x in dir(m) if not x.startswith('_')]
    print(f'  {m.__name__}: {len(pub)} public symbols ({", ".join(pub[:5])}...)')
