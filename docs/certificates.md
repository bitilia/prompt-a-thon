# Certificates

[Wiki home](README.md) · [prompt-a-thon.bitilia.com](https://prompt-a-thon.bitilia.com)

Admins generate certificate PDFs with WeasyPrint (`services/email.py` imports `weasyprint.HTML`). The installer pulls in the Pango and Cairo libraries WeasyPrint needs on Debian. There is no ReportLab or fpdf2 path in this release.

From `/admin/certificates` an admin can preview a PDF and send certificates. Rows record recipient email, name, competition name, school, award type, whether a PDF was generated, and a link to the email log.

WeasyPrint will fail on a machine that only has the Python package and not the system libraries. `flask` will show the import or render error in the log. PDF submissions do not need WeasyPrint. Certificates do.

Award types and the visual template are whatever the admin screen and the HTML template produce. Sending a certificate is a real email to the recipient.
