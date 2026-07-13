"""Manually run health check once to see status."""
import sys, asyncio
sys.path.insert(0, '.')
from moa_gateway.model_pool import get_model_pool

p = get_model_pool()
print('before: ', [(k, e.health_status) for k, e in list(p.endpoints.items())[:3]])

# Manually run health check
async def go():
    await p._health_check_loop()
asyncio.run(go())

print('after: ', [(k, e.health_status) for k, e in list(p.endpoints.items())[:5]])
healthy = sum(1 for e in p.endpoints.values() if e.health_status == 'healthy')
print(f'healthy={healthy}/{len(p.endpoints)}')
