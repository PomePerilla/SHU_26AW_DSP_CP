#pragma once
#include <QApplication>
#include <QColor>
#include <QFile>
#include <QFont>
#include <QPalette>
#include <QDir>
namespace workbench {
inline constexpr int controlHeight = 36;
inline constexpr int rowHeight = 36;
inline constexpr int splitterHit = 5;
inline QFont dataFont() {
    QFont font;
    font.setFamilies({"Segoe UI", "Microsoft YaHei UI", "sans-serif"});
    font.setStyleHint(QFont::Monospace);
    font.setPixelSize(14);
    return font;
}
inline bool applyTheme(QApplication &app, bool dark, const QString &directory) {
    QFile sheet(QDir(directory).filePath(dark ? "theme-dark.qss" : "theme-light.qss"));
    if (!sheet.open(QIODevice::ReadOnly)) return false;
    QPalette p;
    p.setColor(QPalette::Window, QColor(dark ? "#141619" : "#F4F6F8"));
    p.setColor(QPalette::WindowText, QColor(dark ? "#E8ECF1" : "#252B34"));
    p.setColor(QPalette::Base, QColor(dark ? "#1B1E23" : "#FFFFFF"));
    p.setColor(QPalette::AlternateBase, QColor(dark ? "#20242A" : "#F8F9FB"));
    p.setColor(QPalette::Text, QColor(dark ? "#E8ECF1" : "#252B34"));
    p.setColor(QPalette::Button, QColor(dark ? "#22262C" : "#EEF1F5"));
    p.setColor(QPalette::ButtonText, QColor(dark ? "#E8ECF1" : "#252B34"));
    p.setColor(QPalette::Highlight, QColor(dark ? "#303B4B" : "#E6EDF5"));
    p.setColor(QPalette::HighlightedText, QColor(dark ? "#E8ECF1" : "#252B34"));
    p.setColor(QPalette::PlaceholderText, QColor(dark ? "#A0A8B4" : "#626E7E"));
    p.setColor(QPalette::Mid, QColor(dark ? "#30363F" : "#DDE3EA"));
    p.setColor(QPalette::Disabled, QPalette::Text, QColor(dark ? "#747E8B" : "#8D97A5"));
    p.setColor(QPalette::Disabled, QPalette::ButtonText, QColor(dark ? "#747E8B" : "#8D97A5"));
    app.setPalette(p);
    app.setStyleSheet(QString::fromUtf8(sheet.readAll()));
    return true;
}
} // namespace workbench
