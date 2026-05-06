import { createApp } from 'vue'
import axios from 'axios'
import './style.css'
import App from './App.vue'

axios.defaults.withCredentials = true

createApp(App).mount('#app')
