# Повітря і пилок

PoC-проєкт для курсу «Хмарні обчислення» (Завдання 2): моніторинг якості повітря та пилку для міст України на Google Maps Platform.

**API:** Air Quality API · Pollen API · Geocoding API

## Можливості

- Поточний стан: Universal AQI, локальний індекс, головний забруднювач, поради для 7 груп людей
- Динаміка: 48 год історії + 72 год прогнозу
- Пилок: теплова карта Universal Pollen Index на 5 днів
- Карта з тепловими тайлами якості повітря та пилку
- Порівняння шести міст України
- «Коли краще вийти надвір»: найкращі 2-годинні вікна за прогнозом UAQI та пилку

## Запуск

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # вписати GOOGLE_MAPS_API_KEY
streamlit run app.py
```

У Google Cloud Console потрібно увімкнути Air Quality API, Pollen API і Geocoding API та створити API-ключ. Ключ для теплових тайлів потрапляє в браузер, тож його варто обмежити за API та доменом.

Без ключа застосунок працює в режимі replay: показує відповіді, раніше збережені в `data/cache`.

## Тести

```bash
pytest
```

## Структура

| Файл | Призначення |
|---|---|
| `app.py` | Streamlit UI |
| `airpollen/client.py` | HTTP-клієнт до трьох API, пагінація, кеш, обробка помилок |
| `airpollen/analysis.py` | JSON → таблиці pandas, кольори, рекомендації |
| `tests/` | pytest без мережі (фейкова HTTP-сесія) |
