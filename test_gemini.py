import os
import traceback
from google import genai
from app.core.config import settings

def test_gemini():
    print("API Key configured in settings:", settings.GEMINI_API_KEY)
    try:
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents="Hello, this is a test."
        )
        print("Success! Gemini responded:")
        print(response.text)
    except Exception as e:
        print("Error calling Gemini:")
        traceback.print_exc()

if __name__ == '__main__':
    test_gemini()
