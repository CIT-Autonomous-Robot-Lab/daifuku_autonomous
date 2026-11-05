// Copyright 2026 Keita Sekiguchi / nop
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include <memory>
#include <optional>

#include "rclcpp/rclcpp.hpp"
#include "sensor_msgs/msg/imu.hpp"

class ImuThrottle : public rclcpp::Node
{
public:
  ImuThrottle()
  : Node("imu_throttle")
  {
    const auto qos = rclcpp::SensorDataQoS().keep_last(1);
    publisher_ = create_publisher<sensor_msgs::msg::Imu>("imu_out", qos);
    subscription_ = create_subscription<sensor_msgs::msg::Imu>(
      "imu_in", qos,
      [this](sensor_msgs::msg::Imu::ConstSharedPtr message) {
        const auto now = this->now();
        if (last_publish_ && now >= *last_publish_ &&
          (now - *last_publish_).nanoseconds() < 20000000)
        {
          return;
        }
        publisher_->publish(*message);
        last_publish_ = now;
      });
  }

private:
  std::optional<rclcpp::Time> last_publish_;
  rclcpp::Publisher<sensor_msgs::msg::Imu>::SharedPtr publisher_;
  rclcpp::Subscription<sensor_msgs::msg::Imu>::SharedPtr subscription_;
};

int main(int argc, char ** argv)
{
  rclcpp::init(argc, argv);
  rclcpp::spin(std::make_shared<ImuThrottle>());
  rclcpp::shutdown();
  return 0;
}
