"""Настройки источников новостей."""

# Веса компонент управляются через universe.select_component_weights()

# RSS Yahoo: общие новости и макро по индексу (настроение рынка).
DEFAULT_NEWS_FEEDS = [
    "https://finance.yahoo.com/news/rssindex",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US",
    # Доходность UST 10Y — макро-фон для акций и облигаций.
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=^TNX&region=US&lang=en-US",
]

# Google News RSS — гибкий поиск по тикеру (без API-ключа).
GOOGLE_NEWS_RSS_TEMPLATE = (
    "https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en"
)
