"""Общий 3D-просмотрщик моделей: QtWebEngine + ``<model-viewer>``.

Используется главным окном (превью мода) и окном редактора VPK (превью
ресурса). JS-библиотека ``model-viewer`` подгружается с CDN Google, поэтому
для показа модели нужен интернет.
"""

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QColor
from PySide6.QtWebEngineCore import QWebEnginePage


class ModelViewerWebPage(QWebEnginePage):
    """Перенаправляет ``console.log('[3D] …')`` из браузера в консоль Python."""

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        if message.startswith("[3D]"):
            print(message)


def create_model_viewer(parent=None):
    """Создаёт готовый ``QWebEngineView`` с страницей-логгером.

    Импорт ``QtWebEngineWidgets`` отложен: модуль нужен только при первом
    показе 3D-превью.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWebEngineCore import QWebEngineSettings
    from PySide6.QtWebEngineWidgets import QWebEngineView

    view = QWebEngineView(parent)
    view.setPage(ModelViewerWebPage(view))
    view.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
    view.setStyleSheet("background: transparent; border-radius: 10px;")
    view.page().setBackgroundColor(QColor("#17191e"))

    settings = view.settings()
    settings.setAttribute(
        QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
    )
    settings.setAttribute(
        QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True
    )
    return view


def model_viewer_html(glb_path: Path) -> str:
    """HTML-страница с ``<model-viewer>`` для локального GLB-файла."""
    file_url = QUrl.fromLocalFile(str(glb_path)).toString()
    return f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/4.0.0/model-viewer.min.js"></script>
                <style>
                    body, html {{
                        margin: 0; padding: 0; width: 100%; height: 100%;
                        background: #17191e; overflow: hidden;
                    }}
                    model-viewer {{
                        width: 100%; height: 100%; outline: none;
                        --poster-color: transparent;
                    }}
                </style>
            </head>
            <body>
                <model-viewer
                    id="viewer"
                    src="{file_url}"
                    camera-controls
                    auto-rotate
                    auto-rotate-delay="1000"
                    shadow-intensity="1.5"
                    shadow-softness="0.7"
                    environment-image="neutral"
                    camera-orbit="0deg 75deg 105%"
                    min-camera-orbit="auto auto 5%"
                    max-camera-orbit="auto auto 500%"
                    exposure="1.1">
                </model-viewer>

                <script>
                    const viewer = document.getElementById('viewer');

                    viewer.addEventListener('load', () => {{
                        const anims = viewer.availableAnimations || [];

                        console.log("[3D] 📦 Всего анимаций в файле: " + anims.length);

                        if (anims.length > 0) {{
                            console.log("[3D] 📋 Доступные: " + anims.join(', '));

                            // Ищем спокойную стойку (idle, stand, menu) без бега и приседа
                            let bestAnim = anims.find(name => {{
                                const n = name.toLowerCase();
                                return (n.includes('idle') || n.includes('stand') || n.includes('menu'))
                                       && !n.includes('crouch') && !n.includes('run');
                            }});

                            // Если идеальной стойки не нашлось — берём первую
                            if (!bestAnim) {{
                                bestAnim = anims[0];
                            }}

                            viewer.animationName = bestAnim;
                            viewer.play();
                            console.log("[3D] ▶ Включена анимация: " + bestAnim);
                        }} else {{
                            console.log("[3D] ℹ️ В модели нет запечённых анимаций (показывается статичная поза)");
                        }}

                        // Скрываем лишние оверлейные пропы (двери, тулганы и т.д.)
                        const symbols = Object.getOwnPropertySymbols(viewer);
                        const sceneSymbol = symbols.find(s => s.description === 'scene');
                        if (sceneSymbol && viewer[sceneSymbol]) {{
                            const scene = viewer[sceneSymbol];
                            scene.traverse((node) => {{
                                const name = (node.name || '').toLowerCase();
                                if (name.includes('door') || name.includes('block') || name.includes('toolgun')) {{
                                    node.visible = false;
                                }}
                            }});
                        }}
                    }});
                </script>
            </body>
            </html>
            """
