//! Ventana de escritorio de Forja de Sprites.
//!
//! La app es una capa fina: arranca el motor en Python (`motor.servidor`) como
//! proceso hijo, muestra la interfaz de `app/ui/` y cierra el motor al salir.
//! Toda la lógica vive en el motor, que también se puede usar sin la app.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;

use tauri::{Manager, RunEvent};

const PUERTO: &str = "8765";

/// Proceso del motor, si lo arrancó la app (si ya había uno, no se toca).
struct Motor(Mutex<Option<Child>>);

/// Raíz del proyecto: `FORJA_RAIZ` o, en desarrollo, dos niveles sobre `src-tauri`.
fn raiz_proyecto() -> PathBuf {
    std::env::var_os("FORJA_RAIZ")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join(".."))
}

fn arrancar_motor() -> std::io::Result<Child> {
    let raiz = raiz_proyecto();
    let python = std::env::var_os("FORJA_PYTHON")
        .map(PathBuf::from)
        .unwrap_or_else(|| raiz.join(".venv").join("bin").join("python"));
    Command::new(python)
        .args(["-m", "motor.servidor", "--puerto", PUERTO])
        .current_dir(&raiz)
        .spawn()
}

fn main() {
    let app = tauri::Builder::default()
        .setup(|app| {
            let ya_escucha = TcpStream::connect(format!("127.0.0.1:{PUERTO}")).is_ok();
            let hijo = if ya_escucha { None } else { Some(arrancar_motor()?) };
            app.manage(Motor(Mutex::new(hijo)));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("no se pudo iniciar la app");

    app.run(|handle, evento| {
        if let RunEvent::Exit = evento {
            if let Some(mut hijo) = handle.state::<Motor>().0.lock().unwrap().take() {
                let _ = hijo.kill();
                let _ = hijo.wait();
            }
        }
    });
}
