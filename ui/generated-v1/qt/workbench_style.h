#pragma once
#include <QApplication>
#include <QColor>
#include <QFile>
#include <QFont>
#include <QPalette>
#include <QDir>
namespace workbench {
inline constexpr int controlHeight = 30;
inline constexpr int rowHeight = 26;
inline constexpr int splitterHit = 5;
inline QFont dataFont() {
    QFont font;
    font.setFamilies({"Consolas", "Cascadia Mono", "Noto Sans Mono", "monospace"});
    font.setStyleHint(QFont::Monospace);
    font.setPixelSize(13);
    return font;
}
inline bool applyTheme(QApplication &app, bool dark, const QString &directory) {
    QFile sheet(QDir(directory).filePath(dark ? "theme-dark.qss" : "theme-light.qss"));
    if (!sheet.open(QIODevice::ReadOnly)) return false;
    QPalette p;
    p.setColor(QPalette::Window, QColor(dark ? "#111315" : "#F3F4F6"));
    p.setColor(QPalette::WindowText, QColor(dark ? "#E8EAED" : "#24282E"));
    p.setColor(QPalette::Base, QColor(dark ? "#17191C" : "#FFFFFF"));
    p.setColor(QPalette::AlternateBase, QColor(dark ? "#1B1D21" : "#F8F9FA"));
    p.setColor(QPalette::Text, QColor(dark ? "#E8EAED" : "#24282E"));
    p.setColor(QPalette::Button, QColor(dark ? "#1D2024" : "#ECEEF1"));
    p.setColor(QPalette::ButtonText, QColor(dark ? "#E8EAED" : "#24282E"));
    p.setColor(QPalette::Highlight, QColor(dark ? "#18353A" : "#E0F1F2"));
    p.setColor(QPalette::HighlightedText, QColor(dark ? "#E8EAED" : "#24282E"));
    p.setColor(QPalette::PlaceholderText, QColor(dark ? "#A8ADB5" : "#5D6571"));
    p.setColor(QPalette::Mid, QColor(dark ? "#34383E" : "#D4D8DE"));
    p.setColor(QPalette::Disabled, QPalette::Text, QColor(dark ? "#747B85" : "#7B838E"));
    p.setColor(QPalette::Disabled, QPalette::ButtonText, QColor(dark ? "#747B85" : "#7B838E"));
    app.setPalette(p);
    app.setStyleSheet(QString::fromUtf8(sheet.readAll()));
    return true;
}
} // namespace workbench
