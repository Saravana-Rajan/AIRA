import { render } from "preact";
import "./styles/app.css";
import "./styles/dashboard.css";
import "./styles/live.css";
import { App } from "./components/App";
import { forecastLoader, lazyListen } from "./lib/connect";
import { captureMonitorKey } from "./lib/useMonitor";

// A monitor key arrives in the URL fragment (#key=...): it is saved for this tab and taken off the address bar
// before anything renders, so neither the router nor the browser history ever sees it.
captureMonitorKey();

// Firestore (database "aira") loads on demand; the forecast comes from aira-live over HTTP.
render(<App listen={lazyListen()} loadForecast={forecastLoader(import.meta.env)} />, document.getElementById("root")!);
