import sys
import asyncio
from pathlib import Path
import motor.motor_asyncio

# Configure DNS resolver to avoid timeouts from inactive virtual network adapters on Windows
try:
    import dns.resolver
    dns.resolver.default_resolver = dns.resolver.Resolver()
    dns.resolver.default_resolver.nameservers = ['8.8.8.8', '1.1.1.1', '8.8.4.4']
except Exception:
    pass

# Add backend root to sys.path
backend_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_root))

# Enable Motor to run seamlessly across all test event loops
def _current_running_loop(self=None):
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None

motor.motor_asyncio.AsyncIOMotorClient.get_io_loop = _current_running_loop
motor.motor_asyncio.AsyncIOMotorDatabase.get_io_loop = _current_running_loop
motor.motor_asyncio.AsyncIOMotorCollection.get_io_loop = _current_running_loop
