// HumanType: tells the humantype service which window has the focus (KWin 5 and 6).
function report(w) {
    if (!w)
        return;
    callDBus('org.humantype.Daemon', '/org/humantype/Daemon', 'org.humantype.Daemon', 'WindowChanged',
        String(w.resourceClass || ''), String(w.desktopFileName || ''), Boolean(w.fullScreen), Number(w.pid || 0));
}

const activated = workspace.windowActivated || workspace.clientActivated;
activated.connect(report);
report(workspace.activeWindow || workspace.activeClient);
