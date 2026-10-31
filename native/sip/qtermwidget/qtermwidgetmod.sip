// SIP declaration of the subset of QTermWidget used by Bunny Deck.
// Signatures mirror /usr/include/qtermwidget6/qtermwidget.h (QTermWidget 2.x).

%Module(name=qtermwidget, language="C++")

%Import QtWidgets/QtWidgetsmod.sip

class QTermWidget : QWidget
{
%TypeHeaderCode
#include <qtermwidget.h>
%End

public:
    enum ScrollBarPosition
    {
        NoScrollBar,
        ScrollBarLeft,
        ScrollBarRight,
    };

    QTermWidget(int startnow, QWidget *parent /TransferThis/ = 0);
    virtual ~QTermWidget();

    void startShellProgram();
    int getShellPID();
    int getForegroundProcessId();
    void setShellProgram(const QString &program);
    void setArgs(const QStringList &args);
    void setEnvironment(const QStringList &environment);
    void setWorkingDirectory(const QString &dir);

    void setTerminalFont(const QFont &font);
    QFont getTerminalFont();
    void setTerminalOpacity(qreal level);
    void setTerminalBackgroundImage(const QString &backgroundImage);
    void setTerminalBackgroundMode(int mode);
    void setColorScheme(const QString &name);
    QStringList getAvailableColorSchemes();
    static QStringList availableColorSchemes();
    static void addCustomColorSchemeDir(const QString &custom_dir);

    void setHistorySize(int lines);
    int historySize() const;
    void setScrollBarPosition(QTermWidget::ScrollBarPosition);
    void scrollToEnd();
    void sendText(const QString &text);

    void setFlowControlEnabled(bool enabled);
    void setMotionAfterPasting(int);
    int historyLinesCount();
    int screenColumnsCount();
    int screenLinesCount();
    void setSelectionStart(int row, int column);
    void setSelectionEnd(int row, int column);
    QString selectedText(bool preserveLineBreaks = true);

    void setBlinkingCursor(bool blink);
    void setAutoClose(bool);
    QString title() const;
    void setMargin(int);
    int getMargin() const;
    void setBoldIntense(bool boldIntense);
    void setConfirmMultilinePaste(bool confirmMultilinePaste);
    void autoHideMouseAfter(int delay);
    void setTerminalSizeHint(bool enabled);

signals:
    void finished();
    void copyAvailable(bool);
    void termGetFocus();
    void termLostFocus();
    void titleChanged();
    void receivedData(const QString &text);
    void bell(const QString &message);

public slots:
    void copyClipboard();
    void pasteClipboard();
    void pasteSelection();
    void zoomIn();
    void zoomOut();
    void clear();
};
