import json
import logging
from typing import Dict

from django.conf import settings
from google import genai
from google.genai import types

from playlist.models import Video

logger = logging.getLogger("django")
client = genai.Client(api_key=settings.GEMINI_API_KEY)
generate_content_config = types.GenerateContentConfig(
            system_instruction="You are music and song expert. You expertise in labeling music/songs tracks details accurately.",
            safety_settings=[
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                    threshold=types.HarmBlockThreshold.BLOCK_NONE,
                ),
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                    threshold=types.HarmBlockThreshold.BLOCK_NONE,
                ),
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                    threshold=types.HarmBlockThreshold.BLOCK_NONE,
                ),
                types.SafetySetting(
                    category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                    threshold=types.HarmBlockThreshold.BLOCK_NONE,
                ),
            ],
            response_mime_type="application/json",
            response_schema=genai.types.Schema(
                type=genai.types.Type.OBJECT,
                required=["title", "artist", "album", "label", "release_year"],
                properties={
                    "title": genai.types.Schema(
                        type=genai.types.Type.STRING,
                    ),
                    "artist": genai.types.Schema(
                        type=genai.types.Type.STRING,
                    ),
                    "album": genai.types.Schema(
                        type=genai.types.Type.STRING,
                    ),
                    "label": genai.types.Schema(
                        type=genai.types.Type.STRING,
                    ),
                    "release_year": genai.types.Schema(
                        type=genai.types.Type.STRING,
                    ),
                    "cover_art_url": genai.types.Schema(
                        type=genai.types.Type.STRING,
                    ),
                },
            ),
        )

class MetadataParserService:
    """
    Uses a generative AI to extract structured metadata (artist, title)
    from an unstructured video title string.
    """

    def init(self):
        pass

    def extract_metadata_from_title(self, video: Video) -> Dict | None:
        """
        Sends the video title to the AI model and parses the JSON response.
        """
        if not video or not video.title:
            return None

        model = "gemini-flash-latest"  # default model
        if video.duration < 500:
            model = "gemini-flash-latest"
        elif video.duration >= 500 and video.duration < 800:
            model = "gemini-flash-latest"
        elif video.duration >= 800 and video.duration < 3000:
            model = "gemini-flash-lite-latest"

        ######## SET CONTENT ########

        contents = [
            types.Content(
                role="user",
                parts=[
                    types.Part(
                        file_data=types.FileData(
                            file_uri=video.url,
                            mime_type="video/*",
                        )
                    ),
                    types.Part.from_text(
                        text="Extract the artist, title, album, label, and release_year from this video."
                    ),
                ],
            ),
        ]
        if video.duration > 3000:
            contents = [
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(
                            text=f"Extract the artist, title, album, label and release_year from this video title: '{video.title}'"
                        ),
                    ],
                ),
            ]

        logger.info(f"[AI] Querying sing model: {model} for video duration: {video.duration} seconds")
        try:
            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=generate_content_config,
            )

            metadata = response.parsed if response.parsed else json.loads(response.text)

            if "artist" in metadata and "title" in metadata:
                logger.info(f"[AI] Successfully parsed: Artist='{metadata['artist']}', Title='{metadata['title']}'")
                return metadata
            return None

        except Exception as e:
            logger.exception(f"[AI] Failed to parse metadata from title. Error: {e}")
            return None
