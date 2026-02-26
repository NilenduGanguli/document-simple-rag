from fastapi import FastAPI, UploadFile, File, HTTPException
from openai import OpenAI
import base64
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="LLM OCR Service")

api_key = os.getenv("OPENAI_API_KEY")

@app.post("/ocr")
async def perform_ocr(file: UploadFile = File(...)):
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not set")
        
    client = OpenAI(api_key=api_key)
    
    try:
        contents = await file.read()
        base64_image = base64.b64encode(contents).decode('utf-8')

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Extract all text from this image. return only the text content without any markdown formatting."},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=3000
        )
        
        extracted_text = response.choices[0].message.content or ""
        # LLM-based OCR has no per-word confidence; return 0.95 when text was
        # found and 0.0 for an empty response so callers can behave consistently
        # with the pytesseract code path (which returns values in [0.0, 1.0]).
        confidence = 0.95 if extracted_text.strip() else 0.0
        return {"text": extracted_text, "confidence": confidence}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

