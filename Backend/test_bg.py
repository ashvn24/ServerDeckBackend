import asyncio
from fastapi import FastAPI, BackgroundTasks
from fastapi.testclient import TestClient
from app.services.email_service import send_org_creation_email

app = FastAPI()

@app.post("/test")
async def test_endpoint(background_tasks: BackgroundTasks):
    background_tasks.add_task(send_org_creation_email, "ashwinvk77@gmail.com", "TestOrg", "Admin")
    return {"message": "task added"}

client = TestClient(app)
response = client.post("/test")
print(response.json())

# Just wait for loop to finish any background tasks?
# FastAPI TestClient might not wait for background tasks or might run them immediately.
# Let's see if the email service prints anything.
