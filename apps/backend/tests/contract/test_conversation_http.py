from fastapi.testclient import TestClient

from tap.interfaces.http.app import create_app


def test_conversation_routes_are_registered_and_blank_first_message_is_rejected():
    client = TestClient(create_app(validation_mode=True))
    paths = client.app.openapi()["paths"]
    assert "/api/v1/projects/{project_id}/conversations" in paths
    assert "/api/v1/projects/{project_id}/conversations/{conversation_id}/events" in paths
    schema = client.app.openapi()["components"]["schemas"]["ConversationCreateRequest"]
    assert schema["properties"]["message"]["minLength"] == 1
