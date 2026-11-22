## StickerHub setup

1. For a new installation, create/import the MySQL database with `database/stickerhub.sql`.
2. For an existing database imported before this update, run `database/database.sql` once to add the customer and supplier status fields used by the connected pages.
3. Install the Python dependencies: `pip install flask mysql-connector-python werkzeug`.
4. Start the app with `python app.py`, then open `http://127.0.0.1:5000`.

The included login is `admin@stickerhub.com`; use the password generated for that account in your database setup.
