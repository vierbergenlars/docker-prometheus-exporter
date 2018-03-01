FROM python:alpine
COPY ./requirements.txt /exporter/requirements.txt
RUN pip install -r /exporter/requirements.txt
COPY . /exporter
CMD ["python", "/exporter/monitor.py"]
