import { userActions, userState } from '@/store/user'
import { Avatar } from 'antd'
import { useNavigate } from 'react-router-dom'
import { useSnapshot } from 'valtio'
import './footer.scss'

export function Footer() {
  const user = useSnapshot(userState)
  const navigate = useNavigate()
  const initial = user.username?.slice(0, 1).toUpperCase() || '?'

  function logout() {
    userActions.setToken('')
    navigate('/login')
  }

  return (
    <div className="base-layout-footer">
      <Avatar className="avatar" size={32} aria-hidden>
        {initial}
      </Avatar>
      <div className="meta">
        <div className="username" title={user.username || ''}>
          {user.username || '未登录'}
        </div>
        <button className="logout" type="button" onClick={logout}>
          退出登录
        </button>
      </div>
    </div>
  )
}
