#pragma once
#include <QApplication>
#include <QColor>
#include <QFile>
#include <QFont>
#include <QPalette>
#include <QDir>
namespace workbench {
inline constexpr int controlHeight = 32;
inline constexpr int rowHeight = 32;
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
    p.setColor(QPalette::Window, QColor(dark ? "#101012" : "#FAFAFA"));
    p.setColor(QPalette::WindowText, QColor(dark ? "#F4F4F5" : "#18181B"));
    p.setColor(QPalette::Base, QColor(dark ? "#18181B" : "#FFFFFF"));
    p.setColor(QPalette::AlternateBase, QColor(dark ? "#1C1C1F" : "#FAFAFA"));
    p.setColor(QPalette::Text, QColor(dark ? "#F4F4F5" : "#18181B"));
    p.setColor(QPalette::Button, QColor(dark ? "#202023" : "#F4F4F5"));
    p.setColor(QPalette::ButtonText, QColor(dark ? "#F4F4F5" : "#18181B"));
    p.setColor(QPalette::Highlight, QColor(dark ? "#2C2C30" : "#EDEDF0"));
    p.setColor(QPalette::HighlightedText, QColor(dark ? "#F4F4F5" : "#18181B"));
    p.setColor(QPalette::PlaceholderText, QColor(dark ? "#A1A1AA" : "#62626C"));
    p.setColor(QPalette::Mid, QColor(dark ? "#303035" : "#E4E4E7"));
    p.setColor(QPalette::Disabled, QPalette::Text, QColor(dark ? "#71717A" : "#8E8E98"));
    p.setColor(QPalette::Disabled, QPalette::ButtonText, QColor(dark ? "#71717A" : "#8E8E98"));
    app.setPalette(p);
    app.setStyleSheet(QString::fromUtf8(sheet.readAll()));
    return true;
}
} // namespace workbench
