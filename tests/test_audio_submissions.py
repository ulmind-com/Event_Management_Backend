import pytest
import io
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
from app.core.security import create_access_token, get_password_hash
from app.main import app
from httpx import AsyncClient, ASGITransport
from datetime import datetime, timezone, timedelta

@pytest.mark.asyncio
async def test_audio_only_submission_validation():
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client.eventora_db

    # Clean previous test data
    await db["competitions"].delete_many({"title": "Audio Validation Test Comp"})
    await db["competition_registrations"].delete_many({"user_email": "audio_test_user@example.com"})
    await db["competition_submissions"].delete_many({"user_email": "audio_test_user@example.com"})

    # Setup mock user
    await db["users"].update_one(
        {"email": "audio_test_user@example.com"},
        {"$set": {
            "email": "audio_test_user@example.com",
            "full_name": "Audio Tester",
            "password_hash": get_password_hash("test1234"),
            "is_verified": True,
            "registration_complete": True,
            "date_of_birth": "2000-01-01"
        }},
        upsert=True
    )

    admin_token = create_access_token({"sub": "admin@eventora.com", "type": "admin"})
    user_token = create_access_token({"sub": "audio_test_user@example.com", "type": "user"})
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    user_headers = {"Authorization": f"Bearer {user_token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Create Competition with dynamic submission fields
        comp_payload = {
            "title": "Audio Validation Test Comp",
            "description": "Testing audio and link submissions strictly.",
            "category": "Singing",
            "start_date": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            "end_date": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            "registration_start_date": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
            "registration_deadline": (datetime.now(timezone.utc) + timedelta(days=10)).isoformat(),
            "submission_deadline": (datetime.now(timezone.utc) + timedelta(days=15)).isoformat(),
            "min_age": 16,
            "max_age": 40,
            "participation_fee": 0.0,
            "is_published": True,
            "audience_voting_enabled": True,
            "dynamic_submission_fields": [
                {"name": "Audition Audio", "label": "Vocal Recording", "type": "AUDIO", "required": True},
                {"name": "Video Performance", "label": "YouTube / Drive Link", "type": "URL", "required": True}
            ],
            "judging_criteria": [
                {"name": "Vocal Pitch", "max_score": 10.0, "weight": 1.0}
            ]
        }
        r_create = await ac.post("/api/admin/advanced-competitions/", json=comp_payload, headers=admin_headers)
        assert r_create.status_code == 200, f"Create comp failed: {r_create.text}"
        comp_id = r_create.json()["_id"]

        # 2. Register User
        r_reg = await ac.post(f"/api/v1/competitions/{comp_id}/register?rules_accepted=true", headers=user_headers)
        assert r_reg.status_code == 200, f"Register failed: {r_reg.text}"

        # 3. TEST REJECTION: Video file (.mp4) in place of audio
        bad_video = {
            "files": ("performance.mp4", b"\x00\x00\x00\x18ftypmp42fake video data", "video/mp4")
        }
        link_file = {
            "files": ("video_link.txt", b"https://youtu.be/sample123", "text/plain")
        }
        r_bad_video = await ac.post(
            f"/api/v1/competitions/{comp_id}/submissions",
            headers=user_headers,
            data={"field_names": "Audition Audio, Video Performance"},
            files=[
                ("files", ("performance.mp4", b"\x00\x00\x00\x18ftypmp42fake video data", "video/mp4")),
                ("files", ("video_link.txt", b"https://youtu.be/sample123", "text/plain"))
            ]
        )
        assert r_bad_video.status_code == 400
        assert "Direct video file uploads are not supported" in r_bad_video.json()["detail"] or "Only audio files" in r_bad_video.json()["detail"]

        # 4. TEST REJECTION: Image file (.png)
        r_bad_png = await ac.post(
            f"/api/v1/competitions/{comp_id}/submissions",
            headers=user_headers,
            data={"field_names": "Audition Audio, Video Performance"},
            files=[
                ("files", ("photo.png", b"\x89PNG\r\n\x1a\nfake png data", "image/png")),
                ("files", ("video_link.txt", b"https://youtu.be/sample123", "text/plain"))
            ]
        )
        assert r_bad_png.status_code == 400

        # 5. TEST REJECTION: PDF file (.pdf)
        r_bad_pdf = await ac.post(
            f"/api/v1/competitions/{comp_id}/submissions",
            headers=user_headers,
            data={"field_names": "Audition Audio, Video Performance"},
            files=[
                ("files", ("doc.pdf", b"%PDF-1.4 fake pdf data", "application/pdf")),
                ("files", ("video_link.txt", b"https://youtu.be/sample123", "text/plain"))
            ]
        )
        assert r_bad_pdf.status_code == 400

        # 6. TEST REJECTION: Renamed non-audio file (e.g. PDF renamed to song.mp3)
        r_fake_mp3 = await ac.post(
            f"/api/v1/competitions/{comp_id}/submissions",
            headers=user_headers,
            data={"field_names": "Audition Audio, Video Performance"},
            files=[
                ("files", ("song.mp3", b"%PDF-1.4 renamed as mp3", "audio/mpeg")),
                ("files", ("video_link.txt", b"https://youtu.be/sample123", "text/plain"))
            ]
        )
        assert r_fake_mp3.status_code == 400
        assert "PDF document" in r_fake_mp3.json()["detail"]

        # 7. TEST REJECTION: Invalid text link file that is not a URL
        r_bad_link = await ac.post(
            f"/api/v1/competitions/{comp_id}/submissions",
            headers=user_headers,
            data={"field_names": "Audition Audio, Video Performance"},
            files=[
                ("files", ("vocal.mp3", b"ID3\x03\x00\x00\x00\x00\x00\x00\x00vocal audio test", "audio/mpeg")),
                ("files", ("video_link.txt", b"this is not a valid url", "text/plain"))
            ]
        )
        assert r_bad_link.status_code == 400

        # 8. TEST SUCCESS: Valid Audio File (.mp3) + YouTube Link
        r_ok_yt = await ac.post(
            f"/api/v1/competitions/{comp_id}/submissions",
            headers=user_headers,
            data={"field_names": "Audition Audio, Video Performance"},
            files=[
                ("files", ("vocal.mp3", b"ID3\x03\x00\x00\x00\x00\x00\x00\x00real vocal audio bytes", "audio/mpeg")),
                ("files", ("video_link.txt", b"https://www.youtube.com/watch?v=dQw4w9WgXcQ", "text/plain"))
            ]
        )
        assert r_ok_yt.status_code == 200, f"Valid submission failed: {r_ok_yt.text}"
        data_yt = r_ok_yt.json()
        assert "submission_id" in data_yt
        assert data_yt["files"]["Video Performance"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        assert data_yt["files"]["Audition Audio"].startswith("https://") or data_yt["files"]["Audition Audio"].startswith("http://")
        assert "/demo/" not in data_yt["files"]["Audition Audio"], "Should never generate fake demo Cloudinary URL"
        assert settings.CLOUDINARY_CLOUD_NAME in data_yt["files"]["Audition Audio"], "Must be stored in configured Cloudinary cloud"

        # Clean submission for next test
        await db["competition_submissions"].delete_many({"user_email": "audio_test_user@example.com"})

        # 9. TEST REJECTION: Non-MP3 Audio (.wav) is rejected (Strictly MP3-only policy)
        r_bad_wav = await ac.post(
            f"/api/v1/competitions/{comp_id}/submissions",
            headers=user_headers,
            data={"field_names": "Audition Audio, Video Performance"},
            files=[
                ("files", ("audition.wav", b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00", "audio/wav")),
                ("files", ("video_link.txt", b"https://www.youtube.com/watch?v=dQw4w9WgXcQ", "text/plain"))
            ]
        )
        assert r_bad_wav.status_code == 400
        assert "Only MP3 audio files (.mp3) are permitted" in r_bad_wav.json()["detail"]

        # 10. TEST REJECTION: Non-YouTube Link (Google Drive) is rejected for video
        r_bad_drive = await ac.post(
            f"/api/v1/competitions/{comp_id}/submissions",
            headers=user_headers,
            data={"field_names": "Audition Audio, Video Performance"},
            files=[
                ("files", ("vocal.mp3", b"ID3\x03\x00\x00\x00\x00\x00\x00\x00real vocal bytes", "audio/mpeg")),
                ("files", ("video_link.txt", b"https://drive.google.com/file/d/1A2B3C4D/view", "text/plain"))
            ]
        )
        assert r_bad_drive.status_code == 400
        assert "only valid YouTube links" in r_bad_drive.json()["detail"]

        # 11. TEST REJECTION: Direct video upload (.mp4) is rejected
        r_bad_mp4 = await ac.post(
            f"/api/v1/competitions/{comp_id}/submissions",
            headers=user_headers,
            data={"field_names": "Audition Audio, Video Performance"},
            files=[
                ("files", ("vocal.mp3", b"ID3\x03\x00\x00\x00\x00\x00\x00\x00real vocal bytes", "audio/mpeg")),
                ("files", ("clip.mp4", b"\x00\x00\x00\x20ftypisom\x00\x00\x02\x00isomiso2mp41", "video/mp4"))
            ]
        )
        assert r_bad_mp4.status_code == 400
        assert "Direct video file uploads are not supported" in r_bad_mp4.json()["detail"]

        # 12. TEST SUCCESS: MP3 Audio + Short YouTube link (https://youtu.be/dQw4w9WgXcQ)
        r_ok_short_yt = await ac.post(
            f"/api/v1/competitions/{comp_id}/submissions",
            headers=user_headers,
            data={"field_names": "Audition Audio, Video Performance"},
            files=[
                ("files", ("vocal.mp3", b"ID3\x03\x00\x00\x00\x00\x00\x00\x00real vocal bytes", "audio/mpeg")),
                ("files", ("video_link.txt", b"https://youtu.be/dQw4w9WgXcQ", "text/plain"))
            ]
        )
        assert r_ok_short_yt.status_code == 200
        assert r_ok_short_yt.json()["files"]["Video Performance"] == "https://youtu.be/dQw4w9WgXcQ"

        # 13. Clean up
        await ac.delete(f"/api/admin/advanced-competitions/{comp_id}", headers=admin_headers)
