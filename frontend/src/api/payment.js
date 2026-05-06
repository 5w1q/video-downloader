import axios from 'axios'

export async function createCheckoutSession(planType = 'monthly') {
  const res = await axios.post(
    '/api/payment/create-checkout',
    { plan_type: planType },
    { withCredentials: true }
  )
  return res.data.data
}

export async function getOrders() {
  const res = await axios.get('/api/payment/orders', { withCredentials: true })
  return res.data.data
}
