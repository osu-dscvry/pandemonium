import uvicorn
import threading
import asyncio
import app.settings as settings
from app.workers.beatmaps import BeatmapWorker
from app.workers.players import PlayerWorker

# constants for the number of threads per worker type
BEATMAP_WORKER_THREADS = 1
PLAYER_WORKER_THREADS = 1


def start_worker(worker_class):
    """Start a worker of the specified class in its own thread+event-loop."""
    def worker_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # create and initialize a WorkerState inside this thread's event loop
        from app.workers import WorkerState

        state = WorkerState()
        loop.run_until_complete(state.init())

        worker = worker_class(state)
        loop.run_until_complete(worker.run())

    print(f"Starting worker thread for {worker_class.__name__}")

    t = threading.Thread(target=worker_thread, daemon=True)
    t.start()

    return t


async def main() -> int:
    threads = []

    for _ in range(BEATMAP_WORKER_THREADS):
        threads.append(start_worker(BeatmapWorker))

    for _ in range(PLAYER_WORKER_THREADS):
        threads.append(start_worker(PlayerWorker))

    return 0


if __name__ == "__main__":
    asyncio.run(main())

    try:
        uvicorn.run(
            "app.api:app",
            host=settings.HOST,
            port=settings.PORT,
            reload=False
        )
    except KeyboardInterrupt:
        print("Shutting down workers...")