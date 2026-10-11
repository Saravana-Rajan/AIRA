import { render } from "preact";
import "./styles/app.css";
import "./styles/dashboard.css";
import { App } from "./components/App";
import { forecastLoader, lazyListen } from "./lib/connect";

// Firestore (database "aira") loads on demand; the forecast comes from aira-live over HTTP.
render(<App listen={lazyListen()} loadForecast={forecastLoader(import.meta.env)} />, document.getElementById("root")!);
