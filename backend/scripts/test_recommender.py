from app.brain.memory_store import MemoryStore
from app.brain.recommender import Recommender

memory = MemoryStore()
memory.connect()
recommender = Recommender(memory)
print("Brain Recommendation for XAUUSDc / TRENDING_DOWN / NEW_YORK :")
print(recommender.recommend('XAUUSDc', 'TRENDING_DOWN', 'NEW_YORK'))
print("=== Done ===")
