import argparse
import asyncio
import os

async def main(backend_url: str):
    print("This script would normally run eval_security twice (e.g. by setting env vars for the backend to disable LLM/OSV) and compare.")
    print("For demonstration, please run eval_security.py directly or modify the backend to support mock modes.")
    pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", default="http://localhost:8000")
    args = parser.parse_args()
    asyncio.run(main(args.backend))
