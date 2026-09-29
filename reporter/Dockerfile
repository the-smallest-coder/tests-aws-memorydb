# Deliberately a floating tag (newest Python), the same as var.reporter_image's
# default in variables.tf (tests/test_reporter_dependencies.py checks it).
FROM python:slim

WORKDIR /app

# Copy and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all report-generator modules
COPY . ./

# Run the report generator
CMD ["python", "report_generator.py"]
