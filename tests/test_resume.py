from app.resume.service import ResumeService

service = ResumeService()

profile = service.load_resume(
    "Sayeed_Frontend_Developer.docx"
)

print(profile)