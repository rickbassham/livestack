FROM rickbassham/python-astro:latest

RUN mkdir /app
WORKDIR /app

COPY Pipfile /app
COPY Pipfile.lock /app

RUN pipenv install --system --deploy

COPY . /app

CMD [ "python", "main.py" ]
