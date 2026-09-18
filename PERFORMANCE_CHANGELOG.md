# V7.1 Performance changelog

- Persistent model artifacts (memory + joblib disk cache).
- Retraining only when the latest completed daily bar changes.
- Live DAY inference keeps using current gap and intraday overlay.
- Reduced redundant intraday/daily computations.
- News/event/regime/macro/fundamentals caches with different refresh windows.
- Fundamentals are loaded on demand in the UI.
- Scanner defaults reduced to protect Streamlit CPU.
- Auto-refresh options: 5/10/15 minutes.
- WAIT plan now shows no fake Entry/Stop/Target levels.
- Model cache metadata shown in Data health.
