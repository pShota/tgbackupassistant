FROM python:3.8

RUN mkdir /app
WORKDIR /app

COPY backupAssistant.py .
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt
RUN DEBIAN_FRONTEND="noninteractive" apt-get install libmagickwand-dev --no-install-recommends -y

CMD [ "python", "./backupAssistant.py" ]

# build command:
# docker buildx build . --platform linux/amd64 -t pshota/tgbackupassistant:x86_64-0.5 --push