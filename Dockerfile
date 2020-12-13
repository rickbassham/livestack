FROM python:3.8

RUN pip install pipenv

RUN mkdir /app

WORKDIR /app

COPY Pipfile /app
COPY Pipfile.lock /app

RUN pipenv install --system

COPY . /app

CMD [ "python", "main.py" ]
