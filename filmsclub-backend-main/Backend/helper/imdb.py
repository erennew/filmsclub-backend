import httpx
from Backend.config import Telegram

BASE_URL = Telegram.IMDB_API

async def search_title(query: str, type: str):
    async with httpx.AsyncClient() as client:
        url = f"{BASE_URL}/search?query={query}"
        response = await client.get(url)
        if response.status_code != 200:
            raise Exception(f"Request failed with status code {response.status_code}")
        data = response.json()

        if data and 'results' in data:
            for result in data['results']:
                if result.get('type') == type:
                    return result
        return None

async def get_detail(imdb_id: str):
    async with httpx.AsyncClient() as client:
        url = f"{BASE_URL}/title/tt{imdb_id}"
        response = await client.get(url)
        if response.status_code != 200:
            raise Exception(f"Request failed with status code {response.status_code}")
        return response.json()

async def get_season(imdb_id: str, season_id: int, episode_id: int):
    async with httpx.AsyncClient() as client:
        url = f"{BASE_URL}/title/tt{imdb_id}/season/{season_id}"
        response = await client.get(url)
        if response.status_code != 200:
            raise Exception(f"Request failed with status code {response.status_code}")
        data = response.json()

        for episode in data.get('episodes', []):
            if episode.get('no') == str(episode_id):
                return episode
        return None
        
