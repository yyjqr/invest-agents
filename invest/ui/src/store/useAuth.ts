import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { User } from '@/types'
import { clearToken, fetchMe, login as apiLogin, register as apiRegister, setToken } from '@/api/client'

interface AuthState {
  user: User | null
  token: string | null
  isLoading: boolean
  login: (username: string, password: string) => Promise<void>
  register: (username: string, password: string, displayName: string) => Promise<void>
  logout: () => void
  hydrate: () => Promise<void>
}

export const useAuth = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      token: null,
      isLoading: false,

      login: async (username, password) => {
        set({ isLoading: true })
        try {
          const res = await apiLogin(username, password)
          setToken(res.access_token)
          const me = await fetchMe()
          set({ token: res.access_token, user: me, isLoading: false })
        } catch (e) {
          set({ isLoading: false })
          throw e
        }
      },

      register: async (username, password, displayName) => {
        set({ isLoading: true })
        try {
          const res = await apiRegister(username, password, displayName)
          setToken(res.access_token)
          const me = await fetchMe()
          set({ token: res.access_token, user: me, isLoading: false })
        } catch (e) {
          set({ isLoading: false })
          throw e
        }
      },

      logout: () => {
        clearToken()
        set({ user: null, token: null })
      },

      hydrate: async () => {
        const token = localStorage.getItem('invest_token')
        if (!token) return
        try {
          const me = await fetchMe()
          set({ user: me, token })
        } catch {
          clearToken()
          set({ user: null, token: null })
        }
      },
    }),
    { name: 'auth-storage', partialize: (s) => ({ token: s.token }) },
  ),
)
