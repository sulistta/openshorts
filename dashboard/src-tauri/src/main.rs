#[cfg(not(debug_assertions))]
use std::error::Error;
#[cfg(not(debug_assertions))]
use std::fs;
#[cfg(not(debug_assertions))]
use std::sync::Mutex;

use tauri::{AppHandle, Manager, RunEvent, State};
#[cfg(not(debug_assertions))]
use tauri_plugin_shell::process::CommandChild;
#[cfg(not(debug_assertions))]
use tauri_plugin_shell::ShellExt;

const DESKTOP_API_PORT: u16 = 37831;

struct BackendUrl(String);
#[cfg(not(debug_assertions))]
struct BackendChild(Mutex<Option<CommandChild>>);

#[tauri::command]
fn backend_url(state: State<'_, BackendUrl>) -> String {
    state.0.clone()
}

#[cfg(not(debug_assertions))]
fn spawn_backend(app: &AppHandle) -> Result<CommandChild, Box<dyn Error>> {
    let data_dir = app.path().app_data_dir()?;
    fs::create_dir_all(&data_dir)?;
    let args = vec![
        "--port".to_string(),
        DESKTOP_API_PORT.to_string(),
        "--data-dir".to_string(),
        data_dir.to_string_lossy().into_owned(),
    ];
    let (_events, child) = app
        .shell()
        .sidecar("openshorts-backend")?
        .args(args)
        .spawn()?;
    Ok(child)
}

#[cfg(not(debug_assertions))]
fn stop_backend(app: &AppHandle) {
    if let Some(state) = app.try_state::<BackendChild>() {
        if let Ok(mut child) = state.0.lock() {
            if let Some(child) = child.take() {
                let _ = child.kill();
            }
        }
    }
}

#[cfg(debug_assertions)]
fn stop_backend(_app: &AppHandle) {}

fn main() {
    let api_url = format!("http://127.0.0.1:{DESKTOP_API_PORT}");
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .setup(move |app| {
            app.manage(BackendUrl(api_url.clone()));
            #[cfg(not(debug_assertions))]
            app.manage(BackendChild(Mutex::new(Some(spawn_backend(&app.handle())?))));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![backend_url])
        .build(tauri::generate_context!())
        .expect("error while running OpenShorts desktop application");

    app.run(|app_handle, event| {
        if let RunEvent::ExitRequested { .. } = event {
            stop_backend(app_handle);
        }
    });
}
